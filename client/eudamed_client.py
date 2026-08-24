"""HTTP client for the unofficial EUDAMED public (UI) API.

Every endpoint and filter parameter here is reverse-engineered; none is
officially documented. Assumptions are marked as comments at the point of use.

Beyond a plain `requests.get`, the client adds:

1. Retry with exponential backoff. On EUDAMED an HTTP 500 is a normal,
   retryable state (usually a server-side timeout), not a hard failure.
2. A raw response cache under raw_cache/. A request takes 5-10 s, so cached
   bodies also serve as test fixtures.
3. Pagination. `page` is 0-based, page size is capped at 300.
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
# Constants, all measured (see docs/ and PROBE_RESULTS.md)
# --------------------------------------------------------------------------------------

BASE_URL = "https://ec.europa.eu/tools/eudamed/api"
EMDN_URL = "https://webgate.ec.europa.eu/dyna2/emdn/api"
DATALAKE_URL = "https://api.datalake.sante.service.ec.europa.eu/eudamed"

#: The server does not accept a page size above 300; verified by probe 02.
MAX_PAGE_SIZE = 300

#: Status codes worth retrying. 500 is deliberately included — see the module
#: docstring.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: EUDAMED answers **every** request whose User-Agent starts with "python-"
#: with HTTP 502, including the harmless /applicationInfo. The `requests`
#: default ("python-requests/x.y.z") therefore never gets through.
#: Verified 2026-07-30: curl, Postman and custom UA strings pass;
#: "python-requests/2.34.2" and "python-urllib/3.12" do not.
#: Hence a custom, truthful UA — no browser spoofing required.
USER_AGENT = "eudamed-tool/0.1"


class DeviceStatus:
    """Values for the `deviceStatusCode` filter."""

    ON_THE_MARKET = "refdata.device-model-status.on-the-market"
    NO_LONGER_ON_THE_MARKET = "refdata.device-model-status.no-longer-on-the-market"
    NOT_INTENDED_FOR_EU_MARKET = "refdata.device-model-status.not-intended-for-eu-market"


class ActorType:
    """Values for the `actorTypeCode` filter on /eos."""

    MANUFACTURER = "refdata.actor-type.manufacturer"
    AUTHORISED_REPRESENTATIVE = "refdata.actor-type.authorised-representative"
    IMPORTER = "refdata.actor-type.importer"
    SYSTEM_PROCEDURE_PACK_PRODUCER = "refdata.actor-type.system-procedure-pack-producer"


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


class EudamedError(Exception):
    """Base class for all errors raised by this client."""


class EudamedHTTPError(EudamedError):
    """The server answered with an error status."""

    #: Seconds from the `Retry-After` header, if the server sent one.
    retry_after: float | None = None

    def __init__(self, status_code: int, url: str, body: str = "") -> None:
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status_code} für {url}")


class EudamedRetryExhausted(EudamedError):
    """Every attempt failed."""

    def __init__(self, url: str, attempts: int, last_error: Exception) -> None:
        self.url = url
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"{attempts} Versuche fehlgeschlagen für {url}: {last_error}")


def erklaere_fehler(exc: Exception) -> str:
    """Turn a client exception into a readable message (German text).

    Distinguishes the cases that matter operationally: rate limiting (429),
    EUDAMED-side outages (500/502/503/504), missing records (404) and
    connection problems.
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
# Response object
# --------------------------------------------------------------------------------------


@dataclass
class EudamedResponse:
    """A response plus the metadata needed to cache it and to trace which
    query produced which result."""

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
        """Hits of a list response; empty if the payload is not a list page."""
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
# Raw response cache
# --------------------------------------------------------------------------------------


class RawCache:
    """Stores each raw response under raw_cache/<endpoint>/<hash>.json.

    The hash covers URL plus parameters, so it is stable and reproducible.
    The envelope keeps status code, duration and timestamp alongside the body,
    so the age of a record stays visible.
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
        """True for a UUID (36 chars with hyphens) or a ULID (26 chars)."""
        return (len(part) == 36 and part.count("-") == 4) or len(part) == 26

    def _slug(self, url: str) -> str:
        """Directory name per endpoint. IDs are dropped from the path,
        otherwise every device would get its own folder.
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
    """Client for the EUDAMED public (UI) API.

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
        #: Maximum age of a cached response. `None` means unlimited.
        #:
        #: Unlimited is the risky setting: a count served from cache returned
        #: 381 hits while the search right after it found 382 devices. On
        #: certificate data the same staleness reads as "expired" instead of
        #: "valid". Set a limit for live use; leave it open only when stable
        #: fixtures are wanted.
        self.cache_max_age_s = cache_max_age_s
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self.backoff_factor = backoff_factor
        self.backoff_max_s = backoff_max_s
        # requests expects (connect, read). EUDAMED connects fast and responds
        # slowly, hence a short connect and a long read timeout.
        self.timeout = (connect_timeout_s, read_timeout_s)
        self.language = language
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        self.on_event = on_event

    def close(self) -> None:
        """Release the connection pool. Call this when a client is discarded,
        otherwise its socket pool stays open."""
        self.session.close()

    # -- Core ---------------------------------------------------------------------

    @staticmethod
    def _cache_alter_s(cached: dict[str, Any] | None) -> float | None:
        """Age of a cache entry in seconds, or None if no usable date is stored.

        An entry without `fetched_at` counts as unknown age: it is neither
        discarded nor reported as fresh.
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
        """Report a state change to the `on_event` callback, if one is set.

        Event kinds: start, ok, retry, error, cache, cache_veraltet. Exceptions
        raised by the callback are swallowed so they cannot abort a request.
        """
        if self.on_event is None:
            return
        try:
            self.on_event({"kind": kind, **felder})
        except Exception:  # noqa: BLE001
            log.debug("on_event-Rückruf fehlgeschlagen", exc_info=True)

    def _sleep_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Exponential backoff with jitter, in seconds.

        The jitter keeps parallel jobs from hitting the API in lockstep after a
        server error. A `Retry-After` header (sent on 429) takes precedence.
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
            # The header may also carry an HTTP date; that form is not parsed.
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
        """A GET with retry and cache. Every other method goes through here."""
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

                # 4xx other than the retryable ones are hard errors: no retry.
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
                            # A 429 means the server throttled deliberately;
                            # that is a different state from a 500.
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
        """Device search on /devices/udiDiData.

        `query` maps to the `name` parameter, whose matching semantics are
        fuzzy: it does not only hit manufacturer names. Use the named filters
        for targeted searches.

        `page` is 0-based. `page_size` is capped at MAX_PAGE_SIZE.
        `extra_params` passes untested filters through (see probe 05); an
        unknown parameter name is silently dropped and looks like success.
        """
        if page < 0:
            raise ValueError("page ist 0-basiert und darf nicht negativ sein")
        size = min(page_size, MAX_PAGE_SIZE)

        params: dict[str, Any] = {
            "page": page,
            "pageSize": size,
            # `size` duplicates `pageSize`. Which one wins is unknown, so both
            # are always set to the same value.
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
        """Hit count only, without loading the hits.

        Uses `size=1`, the smallest page that still carries `totalElements`.
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
        """Yield all hits across all pages.

        A full unfiltered dump takes roughly 5 hours. Always narrow with
        `cnd_code`/`srn` or set `max_pages`.
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
        """Device detail (D2). Carries cndNomenclatures, but no certificates."""
        return self.request(
            f"/devices/udiDiData/{device_uuid}",
            {"languageIso2Code": self.language},
        )

    def get_basic_udi_by_device(self, device_uuid: str) -> EudamedResponse:
        """Basic UDI detail for a device UUID — this is where certificates live.

        This is the path from a search hit to its certificates. The endpoint
        takes the **device UUID**, not a separate Basic UDI ID; the
        `basicUdiDiDataUlid` from the search result is not needed here.
        openregulatory documents `/devices/basicUdiData/{basicUdiDiId}` without
        saying where that ID comes from; Delapro does not use the endpoint.

        An empty `deviceCertificateInfoList` does **not** mean the device is
        uncertified, only that the manufacturer filed no certificate data in
        EUDAMED. In a 2026-07-30 sample, 3 of 12 devices had data.
        """
        return self.request(
            f"/devices/basicUdiData/udiDiData/{device_uuid}",
            {"languageIso2Code": self.language},
        )

    def get_device_certificates(self, device_uuid: str) -> list[dict[str, Any]]:
        """Certificate list of a device.

        `deviceCertificateInfoListForDisplay` is in practice an identical copy
        and is only used as a fallback for the main list.
        """
        payload = self.get_basic_udi_by_device(device_uuid).data
        if not isinstance(payload, dict):
            return []
        return payload.get("deviceCertificateInfoList") or payload.get(
            "deviceCertificateInfoListForDisplay"
        ) or []

    def get_basic_udi_versions(self, basic_udi_ulid: str) -> EudamedResponse:
        """Version history of a Basic UDI, keyed by the `basicUdiDiDataUlid`
        from the search result."""
        return self.request(
            f"/devices/basicUdiData/{basic_udi_ulid}/versions",
            {"languageIso2Code": self.language},
        )

    # -- Certificates -------------------------------------------------------------

    def search_certificates(
        self,
        *,
        notified_body_srn: str | None = None,
        actor_srn: str | None = None,
        page: int = 0,
        page_size: int = 25,
        extra_params: dict[str, Any] | None = None,
    ) -> EudamedResponse:
        """Certificate search (C1/C2).

        `actor_srn` filters on the manufacturer owning the certificate.
        Measured 2026-08-19 (docs/module-matrix.md): 4,472 -> 1 for a real SRN
        and 0 for an invented one, so it is applied server-side.

        As everywhere in the UI API, an unknown parameter name is silently
        dropped and looks like a successful call. Measure a control probe
        before adding parameters here.
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
        """Certificate detail (C3), including `documents[]`."""
        return self.request(
            f"/certificates/{certificate_uuid}/",
            {"languageIso2Code": self.language},
        )

    def download_document(self, document_uuid: str) -> EudamedResponse:
        """Certificate PDF (C4). `data` is `bytes` here, not JSON."""
        return self.request(
            f"/documents/{document_uuid}/",
            {"languageIso2Code": self.language},
            expect_json=False,
        )

    def get_notified_bodies(self, *, page: int = 0, page_size: int = MAX_PAGE_SIZE) -> EudamedResponse:
        """List of notified bodies (C5)."""
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
        """Economic operator search (A1/A2).

        `name` is undocumented in the reference repos, which list only
        `countryIso2Code`, `actorTypeCode` and `srn` for /eos. Verified
        2026-08-06 against the unfiltered total of 48,830:

            name=Therapanacea   ->      1  (FR-MF-000007672, FR)
            actorName=…         -> 48,830  (silently dropped)
            eoName=…            -> 48,830  (silently dropped)
            txtSearch=…         -> 48,830  (silently dropped)

        Three of four plausible names have no effect — the same trap as in
        docs/filter-matrix.md: a wrong parameter name looks like a successful
        call. Only `name` filters.

        The device module and the actor module are disjoint datasets.
        Therapanacea has been registered as a manufacturer since 2021-06-17
        with zero devices filed: unfindable via /devices/udiDiData, immediately
        present via /eos. Searching devices alone mistakes a coverage gap for
        an empty result.
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
        """Actor detail (A3). The payload sits under `actorDataPublicView`."""
        return self.request(
            f"/actors/{actor_uuid}/publicInformation",
            {"languageIso2Code": self.language},
        )

    # -- Misc ----------------------------------------------------------------

    def get_application_info(self) -> EudamedResponse:
        """Build version. Cheap health check; never served from cache."""
        return self.request("/applicationInfo", use_cache=False)

    def get_emdn_tree(self) -> EudamedResponse:
        """Full EMDN/CND tree from the separate EMDN host. `id=#` is the root."""
        return self.request("/nomenclature", {"id": "#"}, base=EMDN_URL)
