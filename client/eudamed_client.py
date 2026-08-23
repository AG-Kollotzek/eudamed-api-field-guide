"""HTTP-Client für die (inoffizielle) EUDAMED-Public-API.

Alle Endpunkte und Filterparameter hier
sind reverse-engineert — keiner ist offiziell dokumentiert. Wo eine Annahme im Spiel
ist, steht das als Kommentar an der jeweiligen Stelle.

Der Client macht drei Dinge über einen simplen `requests.get` hinaus:

1. Retry mit exponentiellem Backoff. Bei EUDAMED ist ein HTTP 500 ein normaler,
   wiederholbarer Zustand (meist ein serverseitiger Timeout), kein echter Fehler.
2. Jede Rohantwort landet in raw_cache/. Das spart bei der Entwicklung enorm Zeit
   (ein Request dauert 5-10 s) und liefert nebenbei die Fixtures für spätere Tests.
3. Paginierung. `page` ist 0-basiert, `size` ist bei 300 gedeckelt.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# Konstanten, alle gemessen (siehe docs/ und PROBE_RESULTS.md)
# --------------------------------------------------------------------------------------

BASE_URL = "https://ec.europa.eu/tools/eudamed/api"
EMDN_URL = "https://webgate.ec.europa.eu/dyna2/emdn/api"
DATALAKE_URL = "https://api.datalake.sante.service.ec.europa.eu/eudamed"

#: Größer als 300 akzeptiert der Server offenbar nicht (Delapro: "scheinbar").
#: Probe 02 verifiziert das.
MAX_PAGE_SIZE = 300

#: Statuscodes, bei denen ein erneuter Versuch sinnvoll ist. 500 gehört hier
#: ausdrücklich dazu — siehe Modul-Docstring.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: EUDAMED beantwortet **jeden** Request mit einem User-Agent, der mit "python-"
#: beginnt, mit einem HTTP 502 — auch den harmlosen /applicationInfo. Der Default
#: von `requests` ("python-requests/x.y.z") läuft also grundsätzlich ins Leere.
#: Am 2026-07-30 verifiziert: curl-, Postman- und eigene UA-Strings kommen durch,
#: "python-requests/2.34.2" und "python-urllib/3.12" nicht.
#: Deshalb ein eigener, wahrheitsgemäßer UA — kein Browser-Spoofing nötig.
USER_AGENT = "eudamed-tool/0.1"


class DeviceStatus:
    """Werte für den Filter `deviceStatusCode`."""

    ON_THE_MARKET = "refdata.device-model-status.on-the-market"
    NO_LONGER_ON_THE_MARKET = "refdata.device-model-status.no-longer-on-the-market"
    NOT_INTENDED_FOR_EU_MARKET = "refdata.device-model-status.not-intended-for-eu-market"


class ActorType:
    """Werte für den Filter `actorTypeCode` auf /eos."""

    MANUFACTURER = "refdata.actor-type.manufacturer"
    AUTHORISED_REPRESENTATIVE = "refdata.actor-type.authorised-representative"
    IMPORTER = "refdata.actor-type.importer"
    SYSTEM_PROCEDURE_PACK_PRODUCER = "refdata.actor-type.system-procedure-pack-producer"


# --------------------------------------------------------------------------------------
# Fehler
# --------------------------------------------------------------------------------------


class EudamedError(Exception):
    """Basisklasse für alle Fehler dieses Clients."""


class EudamedHTTPError(EudamedError):
    """Der Server hat mit einem Fehlerstatus geantwortet."""

    #: Sekunden aus dem `Retry-After`-Header, falls der Server einen geschickt hat.
    retry_after: float | None = None

    def __init__(self, status_code: int, url: str, body: str = "") -> None:
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status_code} für {url}")


class EudamedRetryExhausted(EudamedError):
    """Alle Versuche sind fehlgeschlagen."""

    def __init__(self, url: str, attempts: int, last_error: Exception) -> None:
        self.url = url
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"{attempts} Versuche fehlgeschlagen für {url}: {last_error}")


def erklaere_fehler(exc: Exception) -> str:
    """Übersetzt technische Ausnahmen in eine Aussage, mit der man etwas anfangen kann.

    EUDAMED fällt regelmäßig aus, und ein Stacktrace hilft dabei niemandem
    weiter. Jede Meldung sagt deshalb: was passiert ist, ob es an uns liegt,
    und was zu tun ist.

    """
    if isinstance(exc, EudamedRetryExhausted):
        ursache = exc.last_error
        status = getattr(ursache, "status_code", None)
        if status == 429:
            return ("EUDAMED bremst uns aus (Rate-Limit, HTTP 429). Alle Versuche sind "
                    "aufgebraucht.\n  → Ein paar Minuten warten und erneut versuchen. "
                    "Bereits geladene Daten sind gespeichert und gehen nicht verloren.")
        if status in (500, 502, 503, 504):
            return (f"EUDAMED antwortet nicht richtig (HTTP {status}) — das ist dort "
                    "leider Alltag und liegt nicht an dieser Anfrage.\n"
                    "  → Später erneut versuchen. Der Fortschritt bleibt erhalten.")
        return (f"Keine Verbindung zu EUDAMED nach {exc.attempts} Versuchen.\n"
                f"  → Internetverbindung prüfen; sonst später erneut versuchen.\n"
                f"  (technisch: {ursache})")

    if isinstance(exc, EudamedHTTPError):
        if exc.status_code == 404:
            return ("EUDAMED kennt diesen Datensatz nicht (HTTP 404). Möglicherweise "
                    "wurde er zurückgezogen.")
        return f"EUDAMED hat mit HTTP {exc.status_code} geantwortet."

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ("Zeitüberschreitung bei der Verbindung zu EUDAMED.\n"
                "  → EUDAMED ist bekanntermaßen langsam; später erneut versuchen.")

    return f"Unerwarteter Fehler: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------------------
# Antwort-Objekt
# --------------------------------------------------------------------------------------


@dataclass
class EudamedResponse:
    """Eine Antwort samt Metadaten — die Metadaten brauchen wir für den Cache und
    für das Nachvollziehen, welche Query zu welchem Ergebnis geführt hat (Phase 2)."""

    url: str
    params: dict[str, Any]
    status_code: int
    elapsed_s: float
    data: Any
    from_cache: bool = False
    cache_path: Path | None = None
    attempts: int = 1
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def content(self) -> list[dict[str, Any]]:
        """Die Treffer einer Listen-Antwort. Leer, wenn es keine Liste ist."""
        if isinstance(self.data, dict):
            return self.data.get("content") or []
        return []

    @property
    def total_elements(self) -> int | None:
        if isinstance(self.data, dict):
            return self.data.get("totalElements")
        return None

    @property
    def is_last_page(self) -> bool:
        if isinstance(self.data, dict):
            return bool(self.data.get("last", True))
        return True


# --------------------------------------------------------------------------------------
# Rohantwort-Cache
# --------------------------------------------------------------------------------------


class RawCache:
    """Legt jede Rohantwort unter raw_cache/<endpunkt>/<hash>.json ab.

    Der Hash geht über URL + Parameter, ist also stabil und wiederauffindbar.
    Gespeichert wird nicht nur der Body, sondern auch Statuscode, Dauer und
    Zeitstempel — sonst lässt sich später nicht mehr beurteilen, wie alt ein
    Datensatz ist.
    """

    def __init__(self, directory: Path | str) -> None:
        self.dir = Path(directory)

    @staticmethod
    def key(url: str, params: dict[str, Any] | None) -> str:
        canonical = url
        if params:
            canonical += "?" + urlencode(sorted(params.items()), doseq=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _is_identifier(part: str) -> bool:
        """UUID (36 Zeichen mit Bindestrichen) oder ULID (26 Zeichen)?"""
        return (len(part) == 36 and part.count("-") == 4) or len(part) == 26

    def _slug(self, url: str) -> str:
        """Ordnername je Endpunkt, damit raw_cache/ navigierbar bleibt.

        IDs fliegen raus — sonst entstünde pro Gerät ein eigener Ordner.
        """
        path = url.replace(BASE_URL, "").replace(EMDN_URL, "emdn").replace(DATALAKE_URL, "datalake")
        parts = [p for p in path.strip("/").split("/") if p and not self._is_identifier(p)]
        return "-".join(parts) or "root"

    def path_for(self, url: str, params: dict[str, Any] | None, suffix: str = ".json") -> Path:
        return self.dir / self._slug(url) / f"{self.key(url, params)}{suffix}"

    def read(self, url: str, params: dict[str, Any] | None) -> dict[str, Any] | None:
        path = self.path_for(url, params)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Cache-Datei unlesbar, wird ignoriert: %s", path)
            return None

    def write(
        self,
        url: str,
        params: dict[str, Any] | None,
        *,
        status_code: int,
        elapsed_s: float,
        data: Any,
        attempts: int,
    ) -> Path:
        path = self.path_for(url, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "url": url,
            "params": params or {},
            "status_code": status_code,
            "elapsed_s": round(elapsed_s, 3),
            "attempts": attempts,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": data,
        }
        path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def write_binary(self, url: str, params: dict[str, Any] | None, content: bytes, suffix: str) -> Path:
        path = self.path_for(url, params, suffix=suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path


# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------


class EudamedClient:
    """Client für die EUDAMED-Public-API.

    >>> client = EudamedClient()
    >>> client.get_application_info().data["buildVersion"]      # doctest: +SKIP
    '2.14.0'
    >>> client.count_devices(cnd_code="Q010601")                # doctest: +SKIP
    412
    """

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        raw_cache_dir: Path | str = "raw_cache",
        use_cache: bool = True,
        cache_max_age_s: float | None = None,
        max_attempts: int = 3,
        backoff_base_s: float = 5.0,
        backoff_factor: float = 2.0,
        backoff_max_s: float = 60.0,
        connect_timeout_s: float = 30.0,
        read_timeout_s: float = 180.0,
        language: str = "en",
        session: requests.Session | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache = RawCache(raw_cache_dir)
        self.use_cache = use_cache
        #: Wie alt eine Cache-Antwort höchstens sein darf. `None` = unbegrenzt.
        #:
        #: Der Cache war ursprünglich ein reines Entwicklungswerkzeug (ein Request
        #: dauert 5–20 s). Im laufenden Betrieb ist „unbegrenzt" aber die
        #: gefährlichste Einstellung: Eine Zählabfrage lieferte 381 Treffer aus
        #: dem Cache, während die Suche gleich darauf 382 Geräte fand. Bei
        #: Zertifikatsdaten hieße derselbe Fehler „abgelaufen" statt „gültig".
        #: Deshalb setzt die Oberfläche eine Frist; Probes und Skripte, die
        #: Fixtures brauchen, lassen sie bewusst offen.
        self.cache_max_age_s = cache_max_age_s
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self.backoff_factor = backoff_factor
        self.backoff_max_s = backoff_max_s
        # requests erwartet (connect, read). EUDAMED verbindet schnell und
        # antwortet langsam — deshalb kurzer Connect-, langer Read-Timeout.
        self.timeout = (connect_timeout_s, read_timeout_s)
        self.language = language
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        self.on_event = on_event

    def close(self) -> None:
        """Gibt den Verbindungspool frei. Nötig geworden mit dem
        Mehrbenutzerbetrieb: Der `Sitzungsverwalter` verdrängt inaktive
        Sitzungen samt Client — ohne close() bliebe je verdrängter Sitzung
        ein Socket-Pool offen."""
        self.session.close()

    # -- Kern ---------------------------------------------------------------------

    @staticmethod
    def _cache_alter_s(cached: dict[str, Any] | None) -> float | None:
        """Alter eines Cache-Eintrags in Sekunden, oder None bei fehlendem Datum.

        Ein Eintrag ohne `fetched_at` stammt aus der Zeit vor dem Zeitstempel und
        gilt als unbekannt alt — er wird nicht verworfen, aber auch nicht als
        frisch ausgegeben.
        """
        if not cached:
            return None
        roh = cached.get("fetched_at")
        if not roh:
            return None
        try:
            gestellt = datetime.fromisoformat(str(roh))
        except ValueError:
            return None
        if gestellt.tzinfo is None:
            gestellt = gestellt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - gestellt).total_seconds()

    def _event(self, kind: str, **felder: Any) -> None:
        """Meldet einen Zustandswechsel an den Beobachter, falls einer gesetzt ist.

        Damit kann eine Oberfläche zeigen, *was gerade passiert* — welche URL,
        wie lange sie schon läuft, ob gerade auf ein Backoff gewartet wird. Ohne
        das ist jede Wartezeit über zehn Sekunden von einem Absturz nicht zu
        unterscheiden.

        Ein Fehler im Rückruf darf die Anfrage nicht abbrechen: Anzeige ist
        Beiwerk, der Abruf ist die Aufgabe.
        """
        if self.on_event is None:
            return
        try:
            self.on_event({"kind": kind, **felder})
        except Exception:  # noqa: BLE001
            log.debug("on_event-Rückruf fehlgeschlagen", exc_info=True)

    def _sleep_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Exponentielles Backoff mit Jitter.

        Der Jitter ist nicht Kosmetik: ohne ihn laufen parallele Jobs nach einem
        Serverfehler synchron wieder auf die API los.

        Schickt der Server einen `Retry-After`-Header (bei 429), hat der Vorrang —
        dann wissen wir genau, wie lange zu warten ist, statt zu raten.
        """
        if retry_after is not None:
            return min(retry_after, self.backoff_max_s) + random.uniform(0, 1)
        delay = min(self.backoff_base_s * (self.backoff_factor ** (attempt - 1)), self.backoff_max_s)
        return delay + random.uniform(0, delay * 0.25)

    @staticmethod
    def _retry_after(response: requests.Response | None) -> float | None:
        if response is None:
            return None
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            # Der Header darf auch ein HTTP-Datum enthalten; das werten wir nicht aus.
            return None

    def request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        base: str | None = None,
        expect_json: bool = True,
        use_cache: bool | None = None,
    ) -> EudamedResponse:
        """Ein GET mit Retry und Cache. Alle anderen Methoden gehen hier durch."""
        url = f"{(base or self.base_url).rstrip('/')}/{path.lstrip('/')}" if path else (base or self.base_url)
        params = {k: v for k, v in (params or {}).items() if v is not None}
        should_cache = self.use_cache if use_cache is None else use_cache

        if should_cache and expect_json:
            cached = self.cache.read(url, params)
            alter = self._cache_alter_s(cached)
            if cached is not None and self.cache_max_age_s is not None \
                    and alter is not None and alter > self.cache_max_age_s:
                log.info("CACHE VERALTET (%.0f s) — wird neu geholt: %s", alter, url)
                self._event("cache_veraltet", url=url, params=params, alter_s=alter)
                cached = None
            if cached is not None:
                log.debug("CACHE HIT %s %s", url, params)
                self._event("cache", url=url, params=params,
                            fetched_at=cached.get("fetched_at", ""), alter_s=alter)
                return EudamedResponse(
                    url=url,
                    params=params,
                    status_code=cached.get("status_code", 200),
                    elapsed_s=cached.get("elapsed_s", 0.0),
                    data=cached.get("data"),
                    from_cache=True,
                    cache_path=self.cache.path_for(url, params),
                    fetched_at=cached.get("fetched_at", ""),
                )

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                log.info("HTTP GET attempt=%d/%d %s %s", attempt, self.max_attempts, url, params or "")
                self._event("start", url=url, params=params, attempt=attempt,
                            max_attempts=self.max_attempts,
                            read_timeout_s=self.timeout[1])
                response = self.session.get(url, params=params, timeout=self.timeout)
                elapsed = time.monotonic() - started

                if response.status_code in RETRYABLE_STATUS:
                    error = EudamedHTTPError(response.status_code, response.url, response.text[:500])
                    error.retry_after = self._retry_after(response)
                    raise error
                response.raise_for_status()

                if expect_json:
                    data = response.json()
                    cache_path = None
                    if should_cache:
                        cache_path = self.cache.write(
                            url, params,
                            status_code=response.status_code,
                            elapsed_s=elapsed,
                            data=data,
                            attempts=attempt,
                        )
                else:
                    data = response.content
                    cache_path = None
                    if should_cache:
                        cache_path = self.cache.write_binary(url, params, response.content, ".bin")

                log.info("HTTP OK  attempt=%d %.1fs %s", attempt, elapsed, url)
                self._event("ok", url=url, params=params, attempt=attempt,
                            elapsed_s=elapsed)
                return EudamedResponse(
                    url=url,
                    params=params,
                    status_code=response.status_code,
                    elapsed_s=elapsed,
                    data=data,
                    cache_path=cache_path,
                    attempts=attempt,
                )

            except (
                EudamedHTTPError,
                requests.Timeout,
                requests.ConnectionError,
                requests.HTTPError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                elapsed = time.monotonic() - started
                last_error = exc

                # 4xx außer den retrybaren sind echte Fehler — nicht wiederholen.
                status = getattr(exc, "status_code", None)
                if status is None and isinstance(exc, requests.HTTPError) and exc.response is not None:
                    status = exc.response.status_code
                    last_error = EudamedHTTPError(status, url, exc.response.text[:500])
                if status is not None and status not in RETRYABLE_STATUS:
                    log.warning("HTTP %s (nicht wiederholbar) nach %.1fs: %s", status, elapsed, url)
                    self._event("error", url=url, params=params, attempt=attempt,
                                elapsed_s=elapsed, status=status, reason=str(exc))
                    raise last_error from exc

                if attempt >= self.max_attempts:
                    self._event("error", url=url, params=params, attempt=attempt,
                                elapsed_s=elapsed, status=status, reason=str(exc))
                    break

                delay = self._sleep_for(attempt, getattr(last_error, "retry_after", None))
                log.warning(
                    "HTTP FEHLER attempt=%d/%d nach %.1fs (%s) — neuer Versuch in %.1fs",
                    attempt, self.max_attempts, elapsed, exc, delay,
                )
                self._event("retry", url=url, params=params, attempt=attempt,
                            max_attempts=self.max_attempts, elapsed_s=elapsed,
                            status=status, delay_s=delay, reason=str(exc),
                            # Ein 429 heißt: der Server hat ausdrücklich gebremst.
                            # Das ist ein anderer Sachverhalt als ein 500 und
                            # gehört in der Anzeige anders benannt.
                            gedrosselt=status == 429)
                time.sleep(delay)

        raise EudamedRetryExhausted(url, self.max_attempts, last_error or RuntimeError("unbekannt"))

    # -- Devices ------------------------------------------------------------------

    def search_devices(
        self,
        query: str = "",
        *,
        cnd_code: str | None = None,
        srn: str | None = None,
        primary_di: str | None = None,
        trade_name: str | None = None,
        device_model: str | None = None,
        device_status: str | None = DeviceStatus.ON_THE_MARKET,
        page: int = 0,
        page_size: int = 25,
        extra_params: dict[str, Any] | None = None,
        use_cache: bool | None = None,
    ) -> EudamedResponse:
        """Geräte-Suche über /devices/udiDiData.

        `query` geht auf den Parameter `name`. Achtung: dessen Semantik ist unscharf —
        er trifft laut Delapros eigenem TODO nicht nur Herstellernamen. Für gezielte
        Suchen die benannten Filter verwenden.

        `page` ist 0-basiert. `page_size` wird bei MAX_PAGE_SIZE gedeckelt.
        `extra_params` ist die Hintertür für noch ungetestete Filter (siehe Probe 05).
        """
        if page < 0:
            raise ValueError("page ist 0-basiert und darf nicht negativ sein")
        size = min(page_size, MAX_PAGE_SIZE)

        params: dict[str, Any] = {
            "page": page,
            "pageSize": size,
            # `size` ist ein Dubletten-Parameter zu `pageSize`. Welcher gewinnt, ist
            # unbekannt, deshalb wie Delapro immer beide identisch setzen.
            "size": size,
            "iso2Code": self.language,
            "languageIso2Code": self.language,
        }
        if query:
            params["name"] = query
        if cnd_code:
            params["cndCode"] = cnd_code
        if srn:
            params["srn"] = srn
        if primary_di:
            params["primaryDi"] = primary_di
        if trade_name:
            params["tradeName"] = trade_name
        if device_model:
            params["deviceModel"] = device_model
        if device_status:
            params["deviceStatusCode"] = device_status
        if extra_params:
            params.update(extra_params)

        return self.request("/devices/udiDiData", params, use_cache=use_cache)

    def count_devices(self, **kwargs: Any) -> int:
        """Nur die Trefferzahl, ohne die Treffer selbst zu laden.

        Der Trick ist `size=1`: kleiner geht nicht, aber es reicht, um an
        `totalElements` zu kommen. Spart bei großen Gruppen Minuten.
        """
        kwargs.pop("page_size", None)
        kwargs.pop("page", None)
        response = self.search_devices(page=0, page_size=1, **kwargs)
        total = response.total_elements
        if total is None:
            raise EudamedError(f"Antwort ohne totalElements: {response.url}")
        return total

    def iter_devices(
        self,
        query: str = "",
        *,
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int | None = None,
        pause_every: int = 100,
        pause_s: float = 1.0,
        **filters: Any,
    ) -> Iterator[dict[str, Any]]:
        """Alle Treffer über alle Seiten.

        Achtung: ein Vollabzug ohne Filter dauert laut Delapro rund 5 Stunden.
        Immer mit `cnd_code`/`srn` einschränken oder `max_pages` setzen.
        """
        page = 0
        while True:
            response = self.search_devices(query, page=page, page_size=page_size, **filters)
            yield from response.content

            page += 1
            if response.is_last_page or not response.content:
                break
            if max_pages is not None and page >= max_pages:
                break
            if pause_every and page % pause_every == 0:
                time.sleep(pause_s)

    def get_device(self, device_uuid: str) -> EudamedResponse:
        """Geräte-Detail (D2). Enthält u. a. cndNomenclatures, aber keine Zertifikate."""
        return self.request(
            f"/devices/udiDiData/{device_uuid}",
            {"languageIso2Code": self.language},
        )

    def get_basic_udi_by_device(self, device_uuid: str) -> EudamedResponse:
        """Basic-UDI-Detail zu einer Geräte-UUID — **hier hängen die Zertifikate**.

        Das ist der Weg vom Suchtreffer zum Zertifikat. Er steht in keinem der beiden
        Referenz-Repos: openregulatory dokumentiert `/devices/basicUdiData/{basicUdiDiId}`
        und verschweigt, woher diese ID kommt; Delapro nutzt den Endpunkt gar nicht.

        Gefunden am 2026-07-30 durch Mitschnitt des UI-Traffics: die EUDAMED-Oberfläche
        ruft beim Klick auf ein Gerät `/devices/basicUdiData/udiDiData/{deviceUuid}` auf —
        man übergibt also die **Geräte-UUID**, keine separate Basic-UDI-ID. Die
        `basicUdiDiDataUlid` aus dem Suchergebnis wird dafür nicht gebraucht.

        ⚠️ Ein leeres `deviceCertificateInfoList` bedeutet **nicht**, dass das Gerät
        unzertifiziert ist — nur, dass der Hersteller in EUDAMED keine Zertifikatsdaten
        hinterlegt hat. In der Stichprobe vom 2026-07-30 hatten 3 von 12 Geräten Daten.
        """
        return self.request(
            f"/devices/basicUdiData/udiDiData/{device_uuid}",
            {"languageIso2Code": self.language},
        )

    def get_device_certificates(self, device_uuid: str) -> list[dict[str, Any]]:
        """Nur die Zertifikatsliste eines Geräts.

        `deviceCertificateInfoListForDisplay` ist in der Praxis eine identische Kopie;
        wir nehmen die Hauptliste und fallen nur zur Sicherheit auf die Kopie zurück.
        """
        payload = self.get_basic_udi_by_device(device_uuid).data
        if not isinstance(payload, dict):
            return []
        return payload.get("deviceCertificateInfoList") or payload.get(
            "deviceCertificateInfoListForDisplay"
        ) or []

    def get_basic_udi_versions(self, basic_udi_ulid: str) -> EudamedResponse:
        """Versionshistorie einer Basic-UDI über die `basicUdiDiDataUlid` aus dem
        Suchergebnis. Ebenfalls aus dem UI-Traffic; für Änderungsverfolgung interessant."""
        return self.request(
            f"/devices/basicUdiData/{basic_udi_ulid}/versions",
            {"languageIso2Code": self.language},
        )

    # -- Zertifikate --------------------------------------------------------------

    def search_certificates(
        self,
        *,
        notified_body_srn: str | None = None,
        actor_srn: str | None = None,
        page: int = 0,
        page_size: int = 25,
        extra_params: dict[str, Any] | None = None,
    ) -> EudamedResponse:
        """Zertifikatssuche (C1/C2).

        `actor_srn` filtert auf den Hersteller, dem das Zertifikat gehört —
        am 2026-08-19 gemessen (MODULE_MATRIX.md): 4.472 -> 1 bei echter SRN,
        0 bei erfundener, also serverseitig wirksam. Wie überall in der
        UI-API gilt: Ein unbekannter Parametername wird stillschweigend
        verworfen und sieht aus wie ein Erfolg — wer hier Parameter ergänzt,
        misst zuerst die Kontrollprobe.
        """
        size = min(page_size, MAX_PAGE_SIZE)
        params: dict[str, Any] = {
            "page": page,
            "pageSize": size,
            "size": size,
            "languageIso2Code": self.language,
        }
        if notified_body_srn:
            params["notifiedBodySrn"] = notified_body_srn
        if actor_srn:
            params["actorSrn"] = actor_srn
        if extra_params:
            params.update(extra_params)
        return self.request("/certificates/search/", params)

    def get_certificate(self, certificate_uuid: str) -> EudamedResponse:
        """Zertifikat-Detail (C3), inklusive `documents[]`."""
        return self.request(
            f"/certificates/{certificate_uuid}/",
            {"languageIso2Code": self.language},
        )

    def download_document(self, document_uuid: str) -> EudamedResponse:
        """Zertifikats-PDF (C4). `data` ist hier `bytes`, kein JSON."""
        return self.request(
            f"/documents/{document_uuid}/",
            {"languageIso2Code": self.language},
            expect_json=False,
        )

    def get_notified_bodies(self, *, page: int = 0, page_size: int = MAX_PAGE_SIZE) -> EudamedResponse:
        """Liste der Benannten Stellen (C5)."""
        size = min(page_size, MAX_PAGE_SIZE)
        return self.request(
            "/ses/notifiedBodies",
            {"page": page, "pageSize": size, "size": size, "languageIso2Code": self.language},
        )

    # -- Actors -------------------------------------------------------------------

    def search_actors(
        self,
        *,
        name: str | None = None,
        srn: str | None = None,
        country: str | None = None,
        actor_type: str | None = None,
        page: int = 0,
        page_size: int = 25,
    ) -> EudamedResponse:
        """Economic-Operator-Suche (A1/A2).

        `name` ist ein **Neufund vom 2026-08-06** und steht in keinem der beiden
        Referenz-Repos; die dortige Doku kennt für /eos nur
        `countryIso2Code`, `actorTypeCode` und `srn`.

        Verifiziert mit Kontrollprobe — die Gesamtzahl ohne Filter ist 48.830:

            name=Therapanacea   ->      1  (FR-MF-000007672, FR)
            actorName=…         -> 48.830  (still verworfen)
            eoName=…            -> 48.830  (still verworfen)
            txtSearch=…         -> 48.830  (still verworfen)

        Drei von vier plausiblen Namen wirken also nicht — dieselbe Falle wie in
        ../FILTER_MATRIX.md: Ein falscher Parametername sieht aus wie ein
        erfolgreicher Aufruf. Nur `name` zählt.

        **Warum dieser Endpunkt wichtig ist.** Das Gerätemodul und das
        Herstellermodul sind getrennte Bestände. Therapanacea ist seit
        2021-06-17 als Hersteller registriert und hat **null Geräte**
        eingetragen — über /devices/udiDiData ist die Firma unauffindbar,
        über /eos steht sie sofort da. Wer nur Geräte durchsucht, hält eine
        Abdeckungslücke für ein leeres Suchergebnis.
        """
        size = min(page_size, MAX_PAGE_SIZE)
        params: dict[str, Any] = {
            "page": page,
            "pageSize": size,
            "size": size,
            "languageIso2Code": self.language,
        }
        if name:
            params["name"] = name
        if srn:
            params["srn"] = srn
        if country:
            params["countryIso2Code"] = country
        if actor_type:
            params["actorTypeCode"] = actor_type
        return self.request("/eos", params)

    def get_actor(self, actor_uuid: str) -> EudamedResponse:
        """Actor-Detail (A3). Nutzdaten liegen unter `actorDataPublicView`."""
        return self.request(
            f"/actors/{actor_uuid}/publicInformation",
            {"languageIso2Code": self.language},
        )

    # -- Sonstiges ----------------------------------------------------------------

    def get_application_info(self) -> EudamedResponse:
        """Versionsstand — taugt als billiger Health-Check."""
        return self.request("/applicationInfo", use_cache=False)

    def get_emdn_tree(self) -> EudamedResponse:
        """Kompletter EMDN-/CND-Baum. `id=#` ist die Wurzel.

        Basis für Phase 3: Haiku darf nur gegen diese Liste matchen.
        """
        return self.request("/nomenclature", {"id": "#"}, base=EMDN_URL)
