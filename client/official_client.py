"""Client for the official EUDAMED Datalake API.

    from client import EudamedClient
    from client.official_client import OfficialClient

    offiziell = OfficialClient(EudamedClient())
    for satz in offiziell.iter_udi(MF_SRN="DE-MF-000006183"):
        ...                                  # 406 devices from ONE request
    codes = offiziell.referenztabelle()      # -203.0 -> "class-iii"

Base: `https://api.datalake.sante.service.ec.europa.eu/eudamed` (see
`docs/official-api.md`). No key, no login.

This is a layer on top of `EudamedClient`, not a second client:
`EudamedClient.request()` takes a `base=` argument, so retries, backoff, raw
cache and event log apply unchanged.

## Three API properties encapsulated here

**1. Two mandatory parameters.** `format` and `api-version` must be sent on
**every** call. Omitting either yields HTTP 400.

**2. The leading space.** Stored nomenclature values carry one: `" L031299"`.
`NOMENCLATURE_CODE=Q010601` therefore returns **zero** rows,
`NOMENCLATURE_CODE=%20Q010601` returns 1081. `iter_udi()` adds the space;
`watch/apiwacht.py` checks whether the quirk still exists, because the
workaround has to go once it does not.

**3. Cursor instead of page numbers.** `page`, `offset`, `limit`, `$top` — all
eight probed variants answer HTTP 400. Each response carries a `nextLink` with
an opaque `$after` token instead. One page holds 1000 records.

## What it cannot do

**No certificates** — not as a filter, not as a field, not as a resource
(`/certificates` answers 404, probe 07). **No prefix search** over the
nomenclature. **No filter** on market status. Risk class and legislation do
filter server-side, but only via the API's own numeric ids (`-10.0` instead of
`class-iii`).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

from client.eudamed_client import DATALAKE_URL, EudamedClient

log = logging.getLogger(__name__)

#: Required on every call. Omitting either yields HTTP 400.
PFLICHTPARAMETER = {"format": "json", "api-version": "v1.0"}

#: Parameters `/udi` accepts. Anything else is rejected with HTTP 400 — the
#: opposite of the UI API's silent discard, and safer: a typo in a parameter
#: name surfaces immediately instead of returning unfiltered results.
ANGENOMMEN = frozenset({
    "PRIMARY_DI", "BASIC_UDI", "MF_SRN", "TRADE_NAME", "DEVICE_NAME",
    "NOMENCLATURE_CODE", "REFERENCE",
    # Measured 2026-08-17: both filter server-side, but only via the API's own
    # numeric ids (`-10.0`, not `class-iii`).
    "RISK_CLASS_ID", "APPLICABLE_LEGISLATION_ID",
})

#: Probed and rejected (HTTP 400). Listed so they are not tried again, and so
#: `watch/apiwacht.py` notices if one of them starts working.
ABGELEHNT = frozenset({
    "MF_NAME", "IMPLANTABLE", "STERILE", "UUID", "DEVICE_STATUS_TYPE_ID",
})

#: Records per page. Not adjustable — every attempt to set a page size ends in
#: HTTP 400.
SEITENGROESSE = 1000

#: Guard against a cursor that never ends. At 1000 records per page this is
#: 50,000 devices.
MAX_SEITEN = 50

#: Page limit for the actor dump. At 1000 records per page and roughly 48,800
#: actors (measured 2026-08-19) 49 pages are needed; the margin allows growth
#: without letting a broken cursor chain page forever.
AKTEURSSEITEN = 80


def als_filter(parsed: Any) -> dict[str, str]:
    """`ParsedQuery` -> parameter names of the official API.

    The two APIs share no field name (`srn` vs `MF_SRN`, `tradeName` vs
    `TRADE_NAME`), so the mapping is separate from the UI-API one. Only
    parameters the API accepts are passed on; market status has no filter and
    must be applied locally.
    """
    filter: dict[str, str] = {}
    if getattr(parsed, "manufacturer_srn", None):
        filter["MF_SRN"] = parsed.manufacturer_srn
    if getattr(parsed, "product_name", None):
        filter["TRADE_NAME"] = parsed.product_name
    if getattr(parsed, "emdn_code", None):
        # Mapped so that a direct call does not silently run without the
        # nomenclature filter. Note there is no prefix search here.
        filter["NOMENCLATURE_CODE"] = parsed.emdn_code

    # Risk class and legislation filter server-side, but only via the API's own
    # numeric ids: `class-iii` is rejected with HTTP 400, `-10.0` returns the
    # four class III devices of the sample manufacturer.
    from client.normalisierung import RECHTSRAHMEN_ID, RISIKOKLASSEN_ID

    for feld, tabelle, ziel in (
            ("risk_class", RISIKOKLASSEN_ID, "RISK_CLASS_ID"),
            ("legislation", RECHTSRAHMEN_ID, "APPLICABLE_LEGISLATION_ID")):
        wert = getattr(parsed, feld, None)
        kennzahl = tabelle.get(str(wert)) if wert else None
        if kennzahl is not None:
            filter[ziel] = str(kennzahl)
    return filter


class OfficialClient:
    """Official Datalake API on top of the shared `EudamedClient` transport."""

    def __init__(self, transport: EudamedClient) -> None:
        self.transport = transport

    # -- The single request path --------------------------------------------

    def _hole(self, pfad: str, params: dict[str, Any]) -> dict[str, Any]:
        """One request against the Datalake, with the mandatory parameters."""
        antwort = self.transport.request(
            pfad, {**PFLICHTPARAMETER, **params}, base=DATALAKE_URL)
        return antwort.data if isinstance(antwort.data, dict) else {}

    # -- Devices ------------------------------------------------------------

    def iter_udi(self, *, grenze: int | None = None,
                 **filter: str) -> Iterator[dict[str, Any]]:
        """All devices matching a filter, page by page via the cursor.

        A `NOMENCLATURE_CODE` gets the leading space prepended if it is missing
        (see module docstring). Unknown parameter names raise `ValueError` here
        instead of reaching the API as HTTP 400, so the caller sees the list of
        accepted names.
        """
        unbekannt = set(filter) - ANGENOMMEN
        if unbekannt:
            raise ValueError(
                f"Die offizielle Schnittstelle kennt {sorted(unbekannt)} nicht. "
                f"Angenommen werden: {sorted(ANGENOMMEN)}. "
                f"Nachweislich abgelehnt: {sorted(ABGELEHNT & unbekannt)}."
                if ABGELEHNT & unbekannt else
                f"Die offizielle Schnittstelle kennt {sorted(unbekannt)} nicht. "
                f"Angenommen werden: {sorted(ANGENOMMEN)}.")

        params = dict(filter)
        code = params.get("NOMENCLATURE_CODE")
        if code and not str(code).startswith(" "):
            params["NOMENCLATURE_CODE"] = f" {code}"

        pfad, geliefert = "/udi", 0
        for seite in range(MAX_SEITEN):
            daten = self._hole(pfad, params)
            saetze = daten.get("value") or []
            for satz in saetze:
                if grenze is not None and geliefert >= grenze:
                    return
                geliefert += 1
                yield satz

            weiter = daten.get("nextLink")
            if not weiter or not saetze:
                return
            pfad, params = self._cursor(weiter, filter)
            log.debug("Datalake-Cursor: Seite %s, bisher %s Datensätze",
                      seite + 2, geliefert)

        log.warning("Cursor nach %s Seiten abgebrochen (%s Datensätze) — "
                    "die Filterlage ist zu weit gefasst.", MAX_SEITEN, geliefert)

    @staticmethod
    def _cursor(weiter: str, filter: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """Split a `nextLink` back into path and parameters.

        The API returns a full URL while the transport composes `base` and path
        itself, so the URL is taken apart and the opaque `$after` value passed
        on as an ordinary parameter. The original filters are re-applied rather
        than trusted to the undocumented cursor token.
        """
        zerlegt = urlparse(weiter)
        pfad = zerlegt.path.split("/eudamed", 1)[-1] or "/udi"
        aus_url = {k: v[0] for k, v in parse_qs(zerlegt.query).items()}
        return pfad, {**filter, **aus_url}

    def hole_geraet(self, primary_di: str) -> dict[str, Any] | None:
        """A single device by its primary UDI-DI, or None."""
        return next(iter(self.iter_udi(PRIMARY_DI=primary_di, grenze=1)), None)

    def hole_hersteller(self, srn: str, *,
                        grenze: int | None = None) -> list[dict[str, Any]]:
        """All devices of one manufacturer, by SRN.

        Measured 2026-08-16: `DE-MF-000006183` returns 406 devices with 60
        fields in 21.9 seconds from **one** request. The same result over the
        UI API takes 812 requests and about 22 minutes, because there the
        detail and certificate levels each cost one request per device.
        """
        return list(self.iter_udi(MF_SRN=srn, grenze=grenze))

    # -- Reference values ---------------------------------------------------

    def referenztabelle(self, sprache: str = "en"
                        ) -> dict[tuple[str, float], str]:
        """(field name, id) -> label, read from `/reference`.

        The API returns its enumerations as negative numeric ids
        (`RISK_CLASS_ID = -203.0`, `APPLICABLE_LEGISLATION_ID = -197.0`); what
        they mean is documented only in `/reference`. Three traps, all measured
        2026-08-17:

        **1. The key is the pair, not the number.** `-155.0` is Mozambique
        under `PLACED_ON_THE_MARKET_ID` and IVD Annex II list A under
        `RISK_CLASS_ID`. Keying by the number alone mixes countries with risk
        classes.

        **2. The table is itself paginated.** 6,718 rows over seven pages, the
        first holding exactly 1,000. Ignoring the cursor yields a table that
        looks complete but has about 260 instead of 292 keys.

        **3. `VALUE` is a translation, not an identifier.** "Classe III",
        "Klasse IIb", "Κατηγορία III" are the same value; stable identifiers
        have to be mapped separately (see `client/normalisierung.py`).
        """
        tabelle: dict[tuple[str, float], str] = {}
        pfad, params = "/reference", {}
        for _ in range(MAX_SEITEN):
            daten = self._hole(pfad, params)
            zeilen = daten.get("value") or []
            for zeile in zeilen:
                feld, kennung = zeile.get("CODE"), zeile.get("ID")
                if not feld or kennung is None:
                    continue
                schluessel = (str(feld), float(kennung))
                # The requested language wins; otherwise the first hit stands,
                # so a key without that translation still has a label.
                if (str(zeile.get("LANGUAGE") or "").lower() == sprache.lower()
                        or schluessel not in tabelle):
                    tabelle[schluessel] = str(zeile.get("VALUE") or "")
            weiter = daten.get("nextLink")
            if not weiter or not zeilen:
                break
            pfad, params = self._cursor(weiter, {})
        return tabelle

    # -- Actors -------------------------------------------------------------

    def iter_actors(self, *, grenze: int | None = None,
                    seiten: int = AKTEURSSEITEN,
                    pause_s: float = 0.0,
                    **filter: str) -> Iterator[dict[str, Any]]:
        """The actor dump, page by page via the cursor.

        Measured 2026-08-19: **without filters** the endpoint returns 1,000
        records per page plus `nextLink`; at roughly 48,800 actors that is 49
        pages. Filter semantics remain unclear — a `NAME=` filter returned zero
        rows — so callers page unfiltered and match locally.

        Compared with the UI actor search this endpoint returns 1,000 instead
        of 300 records per request and carries fields the UI API exposes only
        in the per-actor detail call (website, VAT number) plus the active
        status.

        `pause_s` is a courtesy delay between pages, default 0; a full dump
        should set it explicitly.
        """
        pfad, params, geliefert = "/actors", dict(filter), 0
        for seite in range(seiten):
            daten = self._hole(pfad, params)
            saetze = daten.get("value") or []
            for satz in saetze:
                if grenze is not None and geliefert >= grenze:
                    return
                geliefert += 1
                yield satz

            weiter = daten.get("nextLink")
            if not weiter or not saetze:
                return
            pfad, params = self._cursor(weiter, filter)
            if pause_s:
                time.sleep(pause_s)
            log.debug("Akteurs-Cursor: Seite %s, bisher %s Sätze",
                      seite + 2, geliefert)

        log.warning("Akteursabzug nach %s Seiten abgebrochen (%s Sätze) — "
                    "AKTEURSSEITEN erhöhen.", seiten, geliefert)
