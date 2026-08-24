"""Periodic snapshots of the EUDAMED interface, compared over time.

    python watch/apiwacht.py            # run only if due
    python watch/apiwacht.py --jetzt    # run immediately

The UI API is unofficial: no version guarantee, no changelog, no
announcements. Its most dangerous property is measured in
docs/filter-matrix.md:

> EUDAMED discards unknown parameters silently. HTTP 200, a plausible
> total, no hint -- only unfiltered.

A renamed parameter therefore never surfaces as an error. It surfaces only
as a result count that stops changing when it should change. That is why
this module measures filter effect instead of waiting for failures.

Roughly 34 requests per snapshot, 2 s apart -- about two minutes,
every 14 days:

1. **Do the filters still work?** Every permitted value of a parameter is
   counted separately. Clearly different counts mean the parameter
   filters; identical counts mean it is discarded. Comparing values
   against each other needs neither a stable baseline nor a well-chosen
   reference group (see `FILTER`).
2. **Do the code values still exist?** The same counts answer that. How a
   vanished value shows up depends on what EUDAMED does with unknown
   values, which `KANARIENVOGEL` measures on every run.
3. **Are the fields still named the same?** `tradeName`, `primaryDi`,
   `deviceCertificateInfoList` and the rest. A renamed field yields empty
   data, not an error.
4. **Which data build?** `buildVersion` is the cheapest hint that
   something changed at all.
5. **Does `cndCode` still do prefix search?** (from
   `probes/probe_03_cnd_prefix.py`) A parent code covering its child nodes
   carries every group query.
6. **Is page numbering still 0-based?** (from
   `probes/probe_02_pagination.py`) Otherwise every multi-page result list
   is off by one page.
7. **Are there filters that did not exist before?** (from
   `probes/probe_05_filters.py`) `WUNSCHLISTE` checks parameters that have
   no effect today; `KONTROLLE` proves that "count unchanged" still means
   "ignored".

Limits:

* Meaning changes behind plausible numbers stay invisible. If
  `riskClassCode` came to mean "at least this class", the counts would
  still differ and nothing would be reported.
* New capabilities are found only where someone listed them.
  `WUNSCHLISTE` covers eight parameters; a ninth goes unnoticed.
* Nothing happens between invocations -- no daemon and no cron, by design
  against an unofficial, publicly funded API. The log therefore records
  the **actual** interval, never the planned one.

Structure and volume are kept apart: field names, filter effect and code
values are findings, result counts are context. EUDAMED grows daily, so a
count is only flagged when it **falls** (`RUECKGANG`).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

#: Repo root -- this module lives in watch/, one level down.
WURZEL = Path(__file__).resolve().parent.parent

#: The snapshots themselves: raw, machine-readable, gitignored.
AUFNAHMEN = WURZEL / "output" / "apiwacht"

#: The log: a versioned record of when and how the interface changed.
PROTOKOLL = WURZEL / "docs" / "changelog.md"

#: Intended interval between two snapshots.
ABSTAND_TAGE = 14

#: Delay between two requests. Twice the 1.0 s the client uses for bulk reads
#: (`client/eudamed_client.py`): this measurement is not urgent and should not
#: compete with other traffic against a public API.
PAUSE_S = 2.0

#: Seconds to wait before the first request when running in the background.
VERZOEGERUNG_S = 15.0

#: Snapshot layout. When it changes, a comparison against older snapshots is
#: no longer meaningful: the next snapshot counts as a first one instead of
#: producing a wrong diff.
#:
#: 1 (2026-08-14)  filters, code values, field names, page size
#: 2 (2026-08-14)  plus wishlist, control probe, prefix search, page numbering
#: 3 (2026-08-16)  plus the feature switches of the public site
#: 4 (2026-08-17)  plus the official API
FORMAT = 4

#: Reference group for all measurements: large enough for meaningful numbers,
#: small enough for a fast count query. Same group as probes/probe_05_filters.py,
#: so the values stay comparable.
REFERENZ_CND = "Q010601"

#: The filters under test, with every value that may be set. Each value is
#: counted separately, which answers two questions at once: whether the
#: parameter filters at all (the counts differ) and whether each value still
#: exists.
#:
#: Comparing one reference group with and without a filter does not work
#: (measured 2026-08-14):
#:
#:   * Counts fluctuate. The same group returned 1071 hits and, seconds later,
#:     1079 -- the "filtered" value was ABOVE the unfiltered one. EUDAMED is a
#:     live registry.
#:   * A reference group can be uniform. In Q010601 (dental alloys) almost
#:     everything is class IIa, so a IIa filter removes nothing there no matter
#:     how well it works.
#:
#: Comparing several values against each other has neither problem: it needs no
#: stable baseline and no well-chosen group. Four risk classes returning
#: 1.5 M / 843 k / 496 k / 146 k prove the filter works without further
#: assumptions.
FILTER: dict[str, dict[str, Any]] = {
    "riskClassCode": {
        "zweck": "Risikoklasse",
        "werte": ("refdata.risk-class.class-i", "refdata.risk-class.class-iia",
                  "refdata.risk-class.class-iib", "refdata.risk-class.class-iii"),
    },
    "applicableLegislation": {
        "zweck": "Rechtsrahmen",
        "werte": ("refdata.applicable-legislation.mdr",
                  "refdata.applicable-legislation.mdd",
                  "refdata.applicable-legislation.ivdr"),
    },
    "deviceStatusCode": {
        "zweck": "Marktstatus",
        "werte": ("refdata.device-model-status.on-the-market",
                  "refdata.device-model-status.no-longer-on-the-market",
                  "refdata.device-model-status.not-intended-for-eu-market"),
    },
}

#: Counts fluctuate between two calls; EUDAMED is a live registry. Differences
#: below this share of the total therefore count as "equal". A fluctuation of
#: 0.7 % within one minute has been measured.
TOLERANZ = 0.02

#: Filters that do **not** exist today and whose appearance would change what
#: can be asked. Checking all 35 ineffective parameters from
#: docs/filter-matrix.md every run would be wasteful; these are the ones with a
#: concrete use. Each entry names what it would enable.
#:
#: Without this list the watch would only cover what is already used and would
#: never see what EUDAMED **gains** -- and a registry under construction gains
#: things.
WUNSCHLISTE: dict[str, dict[str, str]] = {
    "countryIso2Code": {
        "wert": "DE",
        "nutzen": "Verfügbarkeit nach Land als Suchfilter statt als Auswertung "
                  "nach einem Detailabruf je Gerät — die mit Abstand teuerste "
                  "Auskunft des Werkzeugs würde billig.",
    },
    "notifiedBodySrn": {
        "wert": "0197",
        "nutzen": "Benannte Stelle auf Geräteebene. Heute nur herstellergenau "
                  "über das Zertifikatsmodul, und damit nie ganz belastbar.",
    },
    "implantable": {
        "wert": "true",
        "nutzen": "Implantierbarkeit als Filter. Heute wird sie über die "
                  "EMDN-Kategorien J und P angenähert — eine Näherung, die "
                  "Zahnimplantate nachweislich verfehlt.",
    },
    "sterile": {
        "wert": "true",
        "nutzen": "Sterile Lieferung als Filter statt als Angabe im Detail.",
    },
    "singleUse": {
        "wert": "true",
        "nutzen": "Einmal- gegen Mehrfachgebrauch als Filter.",
    },
    "expiryDateFrom": {
        "wert": "2027-01-01",
        "nutzen": "Ablaufdatum serverseitig eingrenzen. Heute muss dafür die "
                  "Zertifikatslage jedes einzelnen Geräts geladen werden — bei "
                  "380 Geräten rund 20 Minuten.",
    },
    "versionDateFrom": {
        "wert": "2026-01-01",
        "nutzen": "Neu registrierte Produkte seit einem Stichtag — heute "
                  "unbeantwortbar, weil kein Datumsfilter wirkt.",
    },
    "manufacturerName": {
        "wert": "Braun",
        "nutzen": "Herstellername trennscharf statt über den unscharfen "
                  "Freitextparameter, der auch Produktnamen trifft.",
    },
}

#: A parameter name that certainly does not exist. It backs the assumption
#: behind every "has no effect": EUDAMED discards unknown parameters silently.
#: If that ever changed -- unknown names rejected instead -- then "count
#: unchanged" would mean something else and half of these checks would be
#: silently pointless. Measured on every run, not once (see
#: docs/filter-matrix.md).
KONTROLLE = ("diesenParameterGibtEsNicht", "egal")

#: Feature switches with which EUDAMED controls what appears on the public site
#: at all. Found 2026-08-16 via `/configurationParameters?scope=PUBLIC`; the
#: official UI queries them at startup.
#:
#: They are the counterpart to `WUNSCHLISTE`: not filters that might be added,
#: but whole data sets. Two of them settle questions that would otherwise be
#: guesswork:
#:
#:     ffVigFsn = false   Field safety notices (FSN) are NOT publicly visible.
#:     ffSscpi  = true    The summaries of safety and clinical performance are
#:                        public.
#:
#: A flipped switch changes what the public data can answer, so it belongs in
#: the log. The full table of all twelve values is in docs/official-api.md.
SCHALTER = "/configurationParameters"

#: What a flipped switch means, for the switches where the answer is known.
#: The rest get a generic sentence.
SCHALTER_FOLGEN = {
    "ffVigFsn": "Sicherheitsmeldungen und Rückrufe (FSN) wären damit öffentlich "
                "sichtbar — bisher sind sie es nicht (siehe docs/gotchas.md, "
                "Punkt 9). Jede Auskunft, die sich darauf stützt, gehört dann "
                "geprüft.",
    "ffSscpi": "Die Kurzberichte über Sicherheit und klinische Leistung (SSCP) "
               "wären damit öffentlich. Sie sind die einzige klinische Quelle in "
               "EUDAMED überhaupt.",
    "ffDevice": "Ohne diesen Schalter zeigt die öffentliche Seite keine Geräte "
                "mehr — dann ist die Grundlage der UI-API weg.",
    "ffCertificate": "Betrifft die Zertifikatsanzeige, also die teuerste und "
                     "heikelste Auskunft des Werkzeugs.",
    "ffNomenclature": "Betrifft die EMDN-Nomenklatur, aus der die Vorauswahl der "
                      "Codes kommt.",
    "ffPublicDataApiFunction": "Betrifft die offizielle öffentliche Daten-API "
                               "(api.datalake.sante.service.ec.europa.eu) — die "
                               "dokumentierte Alternative zur inoffiziellen "
                               "Schnittstelle, auf der dieses Werkzeug läuft.",
}

#: Codes of increasing length for the prefix probe. Expected chain:
#: count(Q01) >= count(Q0106) >= count(Q010601), all greater than zero.
PRAEFIXKETTE = ("Q01", "Q0106", "Q010601")

#: A result count is flagged from this drop onwards. Growth is never reported:
#: EUDAMED is filling up, which is the expected state.
RUECKGANG = 0.10

#: A code value that certainly does not exist. It answers what would otherwise
#: have to be guessed: **what does EUDAMED do with an unknown enumeration
#: value?** Three answers are conceivable -- zero hits, all hits, or an error --
#: and which one holds decides how a vanished code value can be recognised at
#: all.
#:
#: Measured 2026-08-14: **HTTP 400.** So the obvious check ("does the value
#: still return hits?") would have been useless: a vanished value returns no
#: answer at all. Measured on every run anyway, because today's answer is no
#: promise for tomorrow.
KANARIENVOGEL = ("riskClassCode", "refdata.risk-class.gibt-es-nicht")


# ---------------------------------------------------------------------------
# The official API -- testable more sharply than the UI API
# ---------------------------------------------------------------------------
#
# The official API is the only one with a published contract, so it can be
# checked **directly** instead of through count comparisons:
#
#     UI API      unknown parameter -> silently discarded
#                 => effect only inferable from the difference of two counts
#     official    unknown parameter -> HTTP 400
#                 => support is a yes/no question
#
# That also makes the counter-probe (`OFFIZIELL_ABGELEHNT`) worth running in
# the other direction: a rejected parameter that suddenly answers 200 means a
# capability has been added.

#: Parameters `client/official_client.py` relies on. All must return HTTP 200;
#: if one stops doing so, the official path breaks.
OFFIZIELL_ANGENOMMEN: dict[str, str] = {
    "MF_SRN": "DE-MF-000006183",
    "PRIMARY_DI": "E4947662361",
    "NOMENCLATURE_CODE": " Q010601",
    # Measured 2026-08-17: docs/official-api.md listed both as "HTTP 400",
    # but the rejection applied to the VALUE (`class-iii`), not the parameter.
    # With the numeric id they filter server-side.
    "RISK_CLASS_ID": "-10.0",
    "APPLICABLE_LEGISLATION_ID": "-197.0",
}

#: Verifiably rejected (HTTP 400). A sudden 200 is not a fault but a new
#: capability: `RISK_CLASS_ID` would make the result list filterable
#: server-side instead of requiring local post-filtering.
OFFIZIELL_ABGELEHNT: dict[str, str] = {
    "IMPLANTABLE": "true",
    "STERILE": "true",
    "MF_NAME": "Brainlab",
    "DEVICE_STATUS_TYPE_ID": "-11.0",
}

#: The leading-space quirk: stored nomenclature values carry one
#: (`" Q010601"`). `official_client.iter_udi` compensates for it. If the quirk
#: disappears, that compensation MUST go, otherwise it searches for a value
#: that does not exist and returns zero records silently.
LEERZEICHEN_PROBE = ("Q010601", " Q010601")

#: The enumeration tables in `client/normalisierung.py` are backed by real data
#: (1,880 comparisons, no deviations -- 2026-08-17). Checked here is only
#: whether `/reference` still carries the keys: a missing key leaves the risk
#: class of officially fetched devices null.
REFERENZ_SCHLUESSEL = (("RISK_CLASS_ID", -10.0),
                       ("APPLICABLE_LEGISLATION_ID", -197.0),
                       ("DEVICE_STATUS_TYPE_ID", -11.0))


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kurz(text: object, zeichen: int = 140) -> str:
    """Shorten an error message to log length. A `requests` connection error
    runs 400 characters and says everything in the first 60."""
    einzeilig = " ".join(str(text).split())
    return einzeilig if len(einzeilig) <= zeichen else einzeilig[:zeichen - 1] + "…"


def _felder(eintrag: Any, praefix: str = "", tiefe: int = 2) -> list[str]:
    """Field names of a record, nested ones joined with a dot.

    Two levels are deliberate: consumers read `deviceStatusType.code` and
    `tradeName.texts`, nothing below. Going deeper would report every
    reordering inside the text blocks as a change.
    """
    if not isinstance(eintrag, dict) or tiefe <= 0:
        return []
    namen: list[str] = []
    for schluessel, wert in eintrag.items():
        pfad = f"{praefix}{schluessel}"
        namen.append(pfad)
        if isinstance(wert, dict):
            namen += _felder(wert, f"{pfad}.", tiefe - 1)
        elif isinstance(wert, list) and wert and isinstance(wert[0], dict):
            namen += _felder(wert[0], f"{pfad}[].", tiefe - 1)
    return sorted(set(namen))


def vollstaendig(aufnahme: dict[str, Any]) -> tuple[bool, str]:
    """Is this snapshot usable as a baseline for the next comparison?

    A snapshot taken during an outage is worse than none: a filter that was
    never measured cannot later stand out as broken, so the comparison would
    report calm. Required is what carries the comparison -- the base count, the
    device-search field names, a result for **every** filter, and the two
    measurements that make code values interpretable at all.
    """
    if not aufnahme.get("gesamt"):
        return False, "Gesamtbestand nicht messbar"
    if not (aufnahme.get("felder") or {}).get("geraetesuche"):
        return False, "keine Feldnamen aus der Gerätesuche"
    offen = [n for n, f in (aufnahme.get("filter") or {}).items()
             if f["zustand"] == "unbekannt"]
    if offen:
        return False, f"Filter ohne Messergebnis: {', '.join(offen)}"
    if not aufnahme.get("kanarienvogel"):
        return False, "Kanarienvogel nicht gemessen — Codewerte wären nicht deutbar"
    if not aufnahme.get("kontrolle"):
        return False, ("Kontrollprobe nicht gemessen — ohne sie ist „Filter wird "
                       "ignoriert“ nicht belegbar")
    if aufnahme.get("praefixsuche") in (None, "unbekannt"):
        return False, "Präfixsuche nicht messbar"
    if not aufnahme.get("schalter"):
        return False, "Schalter der öffentlichen Seite nicht gelesen"
    return True, ""


@dataclass
class Aenderung:
    """A difference between two snapshots."""

    schwere: str   # 'kritisch' | 'auffaellig' | 'hinweis'
    bereich: str
    text: str
    #: What breaks because of it. Without this sentence a finding is only an
    #: observation.
    folge: str = ""


def erhebe(client: Any, pause_s: float = PAUSE_S,
           melde: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Record the state of the interface. Read-only, roughly 34 requests.

    Every failure is stored as a result instead of raised: an API that does not
    answer today is a finding, not a reason to lose the snapshot.
    """
    sagen = melde or (lambda _t: None)

    def pause() -> None:
        time.sleep(pause_s)

    def zaehle(**kwargs: Any) -> int | None:
        try:
            return client.count_devices(device_status=None, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a failure is a measurement
            log.info("Zählabfrage fehlgeschlagen (%s): %s", kwargs, exc)
            return None
        finally:
            pause()

    def zustand(werte: dict[str, int | None], gesamt: int | None) -> str:
        """Does this parameter filter, judged by the spread of its values?

        Deliberately without a reference group: comparing the values against
        each other needs no stable baseline and no group in which the filter
        removes anything at all (see FILTER).
        """
        zahlen = [n for n in werte.values() if n is not None]
        if not gesamt or len(zahlen) < 2:
            return "unbekannt"
        spielraum = gesamt * TOLERANZ
        if max(zahlen) - min(zahlen) > spielraum:
            return "wirkt"
        # All values return the same count. Either the parameter is discarded
        # (then this is the total) or it always matches the same set. Both are
        # a failure.
        return "ignoriert"

    aufnahme: dict[str, Any] = {
        "format": FORMAT,
        "zeitpunkt": _jetzt(),
        "build": None,
        "basis": None,
        "gesamt": None,
        "kanarienvogel": None,
        "kontrolle": None,
        "schalter": {},
        "filter": {},
        "wunschliste": {},
        "vokabular": {},
        "felder": {},
        "praefixsuche": None,
        "praefixkette": {},
        "seitenzaehlung": None,
        "seitengroesse": None,
        "offiziell": {},
        "fehler": [],
    }

    # --- Data build ---------------------------------------------------------
    sagen("Datenstand")
    try:
        info = client.get_application_info()
        aufnahme["build"] = str((info.data or {}).get("buildVersion") or "")
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"applicationInfo: {_kurz(exc)}")
    pause()

    # --- Base count and field names of the device search --------------------
    sagen("Gerätesuche")
    basis = None
    try:
        antwort = client.search_devices(cnd_code=REFERENZ_CND, page_size=1,
                                        device_status=None)
        basis = antwort.total_elements
        treffer = (antwort.content or [None])[0]
        aufnahme["felder"]["geraetesuche"] = _felder(treffer)
        aufnahme["geraet_uuid"] = (treffer or {}).get("uuid")
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Gerätesuche: {_kurz(exc)}")
    finally:
        pause()
    aufnahme["basis"] = basis

    # --- Feature switches of the public site --------------------------------
    sagen("Schalter der öffentlichen Seite")
    try:
        antwort = client.request(SCHALTER, {"scope": "PUBLIC",
                                            "languageIso2Code": "en"})
        aufnahme["schalter"] = {
            e["name"]: bool(e.get("enable"))
            for e in (antwort.data or []) if isinstance(e, dict) and e.get("name")}
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Schalter: {_kurz(exc)}")
    pause()

    # --- The total count as a yardstick -------------------------------------
    # Must come BEFORE everything else: without it neither the filter effect
    # nor a code-value count can be interpreted.
    sagen("Gesamtbestand")
    aufnahme["gesamt"] = zaehle()

    # --- How does EUDAMED react to a value that does not exist? -------------
    sagen("Kanarienvogel (erfundener Codewert)")
    kanarie = zaehle(extra_params={KANARIENVOGEL[0]: KANARIENVOGEL[1]})
    if kanarie is None:
        # Not a measurement error but the result: EUDAMED rejects unknown
        # values (HTTP 400 as of 2026-08-14). A vanished code value then
        # returns nothing at all, which is exactly how it is recognised.
        aufnahme["kanarienvogel"] = "fehler"
    elif aufnahme["gesamt"] and kanarie >= aufnahme["gesamt"] * (1 - TOLERANZ):
        aufnahme["kanarienvogel"] = "alles"
    else:
        aufnahme["kanarienvogel"] = "null"

    # --- Filters and code values in one pass --------------------------------
    #
    # Each permitted value is counted separately. Differing counts mean the
    # parameter filters, identical ones mean it is discarded. Each individual
    # count also shows whether that code value still exists.
    for parameter, eintrag in FILTER.items():
        werte: dict[str, int | None] = {}
        for wert in eintrag["werte"]:
            sagen(f"{parameter}: {wert.rsplit('.', 1)[-1]}")
            werte[wert] = zaehle(extra_params={parameter: wert})
        aufnahme["vokabular"].update(werte)
        aufnahme["filter"][parameter] = {
            "zustand": zustand(werte, aufnahme["gesamt"]),
            "zweck": eintrag["zweck"],
            "treffer": werte,
        }

    # --- The control probe behind every "has no effect" ---------------------
    sagen("Kontrollprobe (erfundener Parametername)")
    kontrolle = zaehle(extra_params={KONTROLLE[0]: KONTROLLE[1]})
    aufnahme["kontrolle"] = (
        "verworfen" if kontrolle is not None and aufnahme["gesamt"]
        and abs(kontrolle - aufnahme["gesamt"]) <= aufnahme["gesamt"] * TOLERANZ
        else "abgelehnt" if kontrolle is None else "wirkt")

    # --- Are there filters now that did not exist before? -------------------
    #
    # The opposite direction of the filter check above: not that nothing is
    # lost, but that nothing new is missed. A registry under construction gains
    # capabilities, and nobody announces them.
    for name, eintrag in WUNSCHLISTE.items():
        sagen(f"Wunschliste: {name}")
        anzahl = zaehle(cnd_code=REFERENZ_CND,
                        extra_params={name: eintrag["wert"]})
        if anzahl is None:
            zustand_w = "abgelehnt"
        elif basis is None:
            zustand_w = "unbekannt"
        elif abs(anzahl - basis) > basis * TOLERANZ:
            zustand_w = "wirkt"
        else:
            zustand_w = "verworfen"
        aufnahme["wunschliste"][name] = {"zustand": zustand_w, "treffer": anzahl}

    # --- Does cndCode still do prefix search? -------------------------------
    #
    # This carries every group query: "all dental products" is one request as
    # long as the parent code covers its child nodes. Without it the EMDN tree
    # would have to be expanded locally and queried leaf by leaf -- dozens of
    # requests instead of one, and no error to show for it.
    kette: dict[str, int | None] = {}
    for code in PRAEFIXKETTE:
        sagen(f"Präfixprobe {code}")
        kette[code] = zaehle(cnd_code=code)
    zahlen = [kette[c] for c in PRAEFIXKETTE]
    aufnahme["praefixsuche"] = (
        "unbekannt" if any(z is None for z in zahlen)
        else "ja" if all(z > 0 for z in zahlen)
             and zahlen == sorted(zahlen, reverse=True)
        else "nein")
    aufnahme["praefixkette"] = kette

    # --- Is page numbering still 0-based? -----------------------------------
    # If it were 1-based, every result list would be off by one page: page 0
    # empty or identical to page 1, with nothing in the list to show it.
    sagen("Seitenzählung")
    try:
        s0 = client.search_devices(cnd_code=REFERENZ_CND, page=0, page_size=5,
                                   device_status=None)
        pause()
        s1 = client.search_devices(cnd_code=REFERENZ_CND, page=1, page_size=5,
                                   device_status=None)
        erste = [g.get("uuid") for g in (s0.content or [])]
        zweite = [g.get("uuid") for g in (s1.content or [])]
        aufnahme["seitenzaehlung"] = (
            "0-basiert" if erste and zweite and erste != zweite else "auffaellig")
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Seitenzählung: {_kurz(exc)}")
    pause()

    # --- Field names of the remaining data sets -----------------------------
    uuid = aufnahme.get("geraet_uuid")
    if uuid:
        sagen("Gerätedetail")
        try:
            aufnahme["felder"]["geraetedetail"] = _felder(
                client.get_device(uuid).data)
        except Exception as exc:  # noqa: BLE001
            aufnahme["fehler"].append(f"Gerätedetail: {_kurz(exc)}")
        pause()

        sagen("Basic-UDI (Zertifikate)")
        try:
            aufnahme["felder"]["basic_udi"] = _felder(
                client.get_basic_udi_by_device(uuid).data)
        except Exception as exc:  # noqa: BLE001
            aufnahme["fehler"].append(f"Basic-UDI: {_kurz(exc)}")
        pause()

    sagen("Zertifikatssuche")
    try:
        antwort = client.search_certificates(page_size=1)
        aufnahme["felder"]["zertifikate"] = _felder((antwort.content or [None])[0])
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Zertifikatssuche: {_kurz(exc)}")
    pause()

    sagen("Herstellersuche")
    try:
        antwort = client.search_actors(name="Braun", page_size=1)
        aufnahme["felder"]["hersteller"] = _felder((antwort.content or [None])[0])
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Herstellersuche: {_kurz(exc)}")
    pause()

    # --- Page size ----------------------------------------------------------
    # 300 is the largest page size the UI API accepts. If the limit drops, the
    # number of requests for the same result list silently multiplies.
    sagen("Seitengröße")
    try:
        antwort = client.search_devices(cnd_code=REFERENZ_CND, page_size=300,
                                        device_status=None)
        aufnahme["seitengroesse"] = len(antwort.content or [])
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Seitengröße: {_kurz(exc)}")
    pause()

    # --- The official API ---------------------------------------------------
    sagen("Offizielle Schnittstelle")
    aufnahme["offiziell"] = _erhebe_offiziell(client, pause, aufnahme["fehler"])

    return aufnahme


def _erhebe_offiziell(client: Any, pause: Callable[[], None],
                      fehler: list[str]) -> dict[str, Any]:
    """The official API. Roughly ten requests.

    Nothing is counted here, it is asked: because this endpoint rejects unknown
    parameters with HTTP 400, every supported parameter is a yes/no question
    rather than a count comparison. It works in both directions -- a rejected
    parameter that suddenly answers is a new capability.
    """
    from client.eudamed_client import DATALAKE_URL

    befund: dict[str, Any] = {
        "angenommen": {}, "abgelehnt": {}, "leerzeichen": {},
        "cursor": None, "seitengroesse": None, "referenz": {},
    }

    def frage(params: dict[str, str]) -> tuple[int | None, Any]:
        """(HTTP status, payload). A failure is a measurement here."""
        try:
            antwort = client.request(
                "/udi", {"format": "json", "api-version": "v1.0", **params},
                base=DATALAKE_URL)
            return antwort.status_code, antwort.data
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            return status, None
        finally:
            pause()

    for name, wert in OFFIZIELL_ANGENOMMEN.items():
        status, daten = frage({name: wert})
        saetze = len((daten or {}).get("value") or []) if daten else 0
        befund["angenommen"][name] = {"status": status, "saetze": saetze}
        # First page: pick up cursor and page size along the way.
        if daten and befund["cursor"] is None and saetze:
            befund["cursor"] = bool((daten or {}).get("nextLink"))
            befund["seitengroesse"] = saetze

    for name, wert in OFFIZIELL_ABGELEHNT.items():
        status, _ = frage({name: wert})
        befund["abgelehnt"][name] = {"status": status}

    # The leading-space quirk: without -> 0 hits, with -> many.
    ohne, mit = LEERZEICHEN_PROBE
    for etikett, wert in (("ohne", ohne), ("mit", mit)):
        _, daten = frage({"NOMENCLATURE_CODE": wert})
        befund["leerzeichen"][etikett] = len((daten or {}).get("value") or []) \
            if daten else None

    # Does /reference still carry the keys the normalisation relies on?
    try:
        from client.official_client import OfficialClient

        tabelle = OfficialClient(client).referenztabelle()
        for schluessel in REFERENZ_SCHLUESSEL:
            befund["referenz"][f"{schluessel[0]}:{schluessel[1]}"] = (
                tabelle.get(schluessel) is not None)
    except Exception as exc:  # noqa: BLE001
        fehler.append(f"Referenztabelle: {_kurz(exc)}")
    pause()

    return befund


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _vergleiche_offiziell(alt: dict[str, Any],
                          neu: dict[str, Any]) -> list[Aenderung]:
    """What changed on the official API.

    Severity follows what breaks:

      * An **accepted** parameter that fails breaks the official path --
        `kritisch`.
      * A **rejected** one that suddenly answers is a new capability --
        `hinweis`.
      * The **leading-space quirk** disappearing is more dangerous than it
        sounds: `official_client.py` prepends a space to the value, and
        without the quirk it finds nothing -- silently.
      * A missing **reference key** leaves the risk class of officially
        fetched devices null.
    """
    aenderungen: list[Aenderung] = []
    if not alt or not neu:
        return aenderungen

    for name, jetzt in (neu.get("angenommen") or {}).items():
        vorher = (alt.get("angenommen") or {}).get(name)
        if not vorher:
            continue
        if vorher.get("status") == 200 and jetzt.get("status") != 200:
            aenderungen.append(Aenderung(
                "kritisch", "Offizielle Schnittstelle",
                f"`{name}` wird nicht mehr angenommen "
                f"(HTTP {vorher.get('status')} -> {jetzt.get('status')}).",
                "Der offizielle Zusatzpfad bricht. `client/capabilities.py` "
                "muss den Parameter aus OFFIZIELL.serverfilter nehmen, sonst "
                "läuft jede Abfrage dorthin in einen Fehler."))

    for name, jetzt in (neu.get("abgelehnt") or {}).items():
        vorher = (alt.get("abgelehnt") or {}).get(name)
        if not vorher:
            continue
        if vorher.get("status") != 200 and jetzt.get("status") == 200:
            aenderungen.append(Aenderung(
                "hinweis", "Offizielle Schnittstelle",
                f"`{name}` wird jetzt ANGENOMMEN (HTTP "
                f"{vorher.get('status')} -> 200) — eine neue Fähigkeit.",
                "In OFFIZIELL.serverfilter aufnehmen. Bei RISK_CLASS_ID oder "
                "IMPLANTABLE entfiele damit das lokale Nachfiltern (Fall C)."))

    alt_l, neu_l = alt.get("leerzeichen") or {}, neu.get("leerzeichen") or {}
    if alt_l and neu_l and alt_l.get("ohne") == 0 and (neu_l.get("ohne") or 0) > 0:
        aenderungen.append(Aenderung(
            "kritisch", "Offizielle Schnittstelle",
            "Der Leerzeichen-Fehler in NOMENCLATURE_CODE ist behoben — die "
            f"Abfrage ohne führendes Leerzeichen liefert jetzt "
            f"{neu_l['ohne']} Datensätze statt null.",
            "Die Kapselung in `client/official_client.py:iter_udi` stellt dem "
            "Wert weiterhin ein Leerzeichen voran und findet damit womöglich "
            "nichts mehr. Sie MUSS entfernt werden."))

    for schluessel, da in (neu.get("referenz") or {}).items():
        if (alt.get("referenz") or {}).get(schluessel) and not da:
            aenderungen.append(Aenderung(
                "kritisch", "Offizielle Schnittstelle",
                f"Der Referenzschlüssel `{schluessel}` steht nicht mehr in "
                f"/reference.",
                "Die Zuordnungstabelle in `client/normalisierung.py` läuft "
                "für diesen Wert ins Leere — betroffene Geräte bekämen keine "
                "Risikoklasse mehr, statt einer falschen. Tabelle prüfen."))

    if alt.get("cursor") and not neu.get("cursor"):
        aenderungen.append(Aenderung(
            "auffaellig", "Offizielle Schnittstelle",
            "Die erste Seite trägt kein `nextLink` mehr.",
            "Entweder ist die Paginierung umgestellt (dann holt "
            "`iter_udi` nur noch die erste Seite und schweigt darüber) oder "
            "die Ergebnismenge passt neuerdings auf eine Seite."))

    return aenderungen


def vergleiche(alt: dict[str, Any], neu: dict[str, Any]) -> list[Aenderung]:
    """What changed since the last snapshot.

    Checks run in order of importance, and severity follows what breaks, not
    how large the change looks.
    """
    aenderungen: list[Aenderung] = []

    if alt.get("format") != neu.get("format"):
        return [Aenderung(
            "hinweis", "Aufnahme",
            f"Das Format der Aufnahme hat sich geändert "
            f"({alt.get('format')} -> {neu.get('format')}).",
            "Diese Aufnahme gilt als Erstaufnahme; verglichen wird erst wieder "
            "mit der nächsten.")]

    # --- The official API ---------------------------------------------------
    # Before the filters, because its answers are hard evidence: a 400 instead
    # of a 200 is a fact, not an inference from two counts.
    aenderungen += _vergleiche_offiziell(alt.get("offiziell") or {},
                                         neu.get("offiziell") or {})

    # --- Filters: the critical part -----------------------------------------
    for name, neuer in (neu.get("filter") or {}).items():
        alter = (alt.get("filter") or {}).get(name)
        if not alter:
            continue
        if alter["zustand"] == "wirkt" and neuer["zustand"] == "ignoriert":
            aenderungen.append(Aenderung(
                "kritisch", "Filter",
                f"`{name}` wirkt nicht mehr — alle Werte liefern dieselbe "
                f"Trefferzahl.",
                f"Suchen nach {neuer['zweck']} liefern ab sofort ungefilterte "
                f"Listen, ohne Fehlermeldung. Der Parametername hat sich "
                f"vermutlich geändert; die Kandidaten stehen in "
                f"../FILTER_MATRIX.md."))
        elif alter["zustand"] == "ignoriert" and neuer["zustand"] == "wirkt":
            aenderungen.append(Aenderung(
                "auffaellig", "Filter",
                f"`{name}` wirkt wieder.",
                f"Die Einschränkung nach {neuer['zweck']} ist wieder verlässlich."))

    # --- Feature switches of the public site --------------------------------
    #
    # A switch turning on is the only finding here that announces a new
    # CAPABILITY rather than damage, hence `auffaellig` and not `kritisch`.
    for name, jetzt in (neu.get("schalter") or {}).items():
        vorher = (alt.get("schalter") or {}).get(name)
        if vorher is None or vorher == jetzt:
            continue
        folge = SCHALTER_FOLGEN.get(name, "")
        aenderungen.append(Aenderung(
            "auffaellig", "Öffentliche Seite",
            f"`{name}` steht jetzt auf {'AN' if jetzt else 'AUS'} (vorher "
            f"{'AN' if vorher else 'AUS'}).",
            folge or "Was dieser Schalter freigibt, steht in der Beschreibung "
                     "des Parameters — bitte einmal nachsehen."))
    neue_schalter = sorted(set(neu.get("schalter") or {})
                           - set(alt.get("schalter") or {}))
    if neue_schalter and alt.get("schalter"):
        aenderungen.append(Aenderung(
            "hinweis", "Öffentliche Seite",
            f"Neue Schalter: {', '.join(f'`{s}`' for s in neue_schalter)}.",
            "EUDAMED hat der öffentlichen Seite etwas hinzugefügt. Ob es das "
            "Werkzeug betrifft, entscheidet ein Blick in die Beschreibung."))

    # --- The control probe: the assumption under every "has no effect" ------
    if (alt.get("kontrolle") == "verworfen"
            and neu.get("kontrolle") not in (None, "verworfen")):
        aenderungen.append(Aenderung(
            "auffaellig", "Grundannahme",
            f"EUDAMED verwirft unbekannte Parameter nicht mehr stillschweigend "
            f"(Kontrollprobe: {alt['kontrolle']} -> {neu['kontrolle']}).",
            "Damit bedeutet „Trefferzahl unverändert“ nicht mehr „Filter wird "
            "ignoriert“. Die Aussagen dieser Wacht zu wirkungslosen Parametern "
            "gehören neu bewertet — und ../FILTER_MATRIX.md ebenso."))

    # --- Wishlist: what did not exist before --------------------------------
    for name, neuer in (neu.get("wunschliste") or {}).items():
        alter = (alt.get("wunschliste") or {}).get(name)
        if not alter or alter["zustand"] == neuer["zustand"]:
            continue
        if neuer["zustand"] == "wirkt":
            aenderungen.append(Aenderung(
                "auffaellig", "Neuer Filter",
                f"`{name}` wirkt jetzt — vorher {alter['zustand']} "
                f"({neuer['treffer']} statt {alt.get('basis')} Treffer).",
                WUNSCHLISTE.get(name, {}).get("nutzen", "")
                + " Bitte inhaltlich gegenprüfen: Eine veränderte Trefferzahl "
                  "belegt noch nicht, dass richtig gefiltert wird."))
        elif alter["zustand"] == "wirkt":
            aenderungen.append(Aenderung(
                "kritisch", "Neuer Filter",
                f"`{name}` wirkt nicht mehr (jetzt {neuer['zustand']}).",
                "Falls das Werkzeug diesen Filter inzwischen nutzt, sucht es ab "
                "sofort ungefiltert."))

    # --- Prefix search and page numbering -----------------------------------
    if alt.get("praefixsuche") == "ja" and neu.get("praefixsuche") == "nein":
        aenderungen.append(Aenderung(
            "kritisch", "Präfixsuche",
            f"`cndCode` erfasst die Unterknoten nicht mehr "
            f"({neu.get('praefixkette')}).",
            "Die gesamte Gruppensuche beruht darauf. „Alle Dentalprodukte“ "
            "liefert ab sofort nur noch die Geräte, die genau auf dem "
            "Elterncode registriert sind — eine sehr viel kürzere Liste, der "
            "man nichts ansieht."))

    if (alt.get("seitenzaehlung") == "0-basiert"
            and neu.get("seitenzaehlung") not in (None, "0-basiert")):
        aenderungen.append(Aenderung(
            "kritisch", "Seitenzählung",
            "Seite 0 und Seite 1 liefern dasselbe — die Zählung ist "
            "offenbar nicht mehr 0-basiert.",
            "Jede mehrseitige Trefferliste wäre um eine Seite versetzt: erste "
            "Seite doppelt, letzte fehlt."))

    # --- Vocabulary ---------------------------------------------------------
    #
    # How a vanished code value shows up depends on what EUDAMED does with an
    # unknown value, which the canary measures on every run:
    #
    #   canary == 0        unknown values filter -> 0 is the signal
    #   canary == total    unknown values are ignored -> "all hits" is the
    #                      signal
    #
    # Without that distinction the check would be useless in the second case: a
    # vanished value would return millions of hits and look like a
    # particularly successful filter.
    kanarie, gesamt = neu.get("kanarienvogel"), neu.get("gesamt")
    for wert, anzahl in (neu.get("vokabular") or {}).items():
        vorher = (alt.get("vokabular") or {}).get(wert)
        if vorher is None or not vorher:
            continue
        if kanarie == "fehler":
            verschwunden = anzahl is None
        elif kanarie == "alles":
            verschwunden = (anzahl is not None and gesamt
                            and anzahl >= gesamt * (1 - TOLERANZ))
        else:
            verschwunden = anzahl == 0
        if verschwunden:
            aenderungen.append(Aenderung(
                "kritisch", "Codewert",
                f"`{wert}` kennt EUDAMED nicht mehr "
                f"(vorher {vorher} Treffer, jetzt "
                f"{'keine Antwort' if anzahl is None else f'{anzahl}'}).",
                "Der Wert steht fest im Planner. Eine Frage danach beantwortet "
                "das Werkzeug jetzt mit „nichts gefunden“ — was wie eine "
                "Auskunft aussieht, aber keine ist."))

    if alt.get("kanarienvogel") and kanarie and alt["kanarienvogel"] != kanarie:
        aenderungen.append(Aenderung(
            "auffaellig", "Codewert",
            f"EUDAMED behandelt unbekannte Codewerte anders als bisher "
            f"({alt['kanarienvogel']} -> {kanarie}).",
            "Für sich harmlos, aber es ändert, woran ein verschwundener "
            "Codewert zu erkennen ist. Die Prüfung stellt sich automatisch "
            "darauf ein."))

    # --- Field names --------------------------------------------------------
    for bestand, felder in (neu.get("felder") or {}).items():
        vorher = set((alt.get("felder") or {}).get(bestand) or [])
        jetzt = set(felder or [])
        if not vorher or not jetzt:
            continue
        verschwunden = sorted(vorher - jetzt)
        dazu = sorted(jetzt - vorher)
        if verschwunden:
            aenderungen.append(Aenderung(
                "kritisch", "Feldnamen",
                f"{bestand}: {len(verschwunden)} Feld(er) verschwunden — "
                + ", ".join(f"`{f}`" for f in verschwunden[:8]),
                "Felder, die der Import liest, kommen leer zurück. Das erzeugt "
                "keine Fehlermeldung, sondern leere Spalten — und eine leere "
                "Spalte liest sich wie „keine Angabe in EUDAMED“."))
        if dazu:
            aenderungen.append(Aenderung(
                "hinweis", "Feldnamen",
                f"{bestand}: {len(dazu)} Feld(er) neu — "
                + ", ".join(f"`{f}`" for f in dazu[:8]),
                "Kein Schaden. Möglicherweise aber eine neue Auskunft, die das "
                "Werkzeug bisher nicht anbietet."))

    # --- Data build and volumes ---------------------------------------------
    if alt.get("build") and neu.get("build") and alt["build"] != neu["build"]:
        aenderungen.append(Aenderung(
            "auffaellig", "Datenstand",
            f"buildVersion {alt['build']} -> {neu['build']}.",
            "Für sich genommen harmlos. Zusammen mit einem der Befunde oben ist "
            "es die Erklärung dafür."))

    alt_basis, neu_basis = alt.get("basis"), neu.get("basis")
    if alt_basis and neu_basis and neu_basis < alt_basis * (1 - RUECKGANG):
        aenderungen.append(Aenderung(
            "auffaellig", "Menge",
            f"Die Referenzgruppe {REFERENZ_CND} ist von {alt_basis} auf "
            f"{neu_basis} Treffer gefallen ({(1 - neu_basis / alt_basis):.0%}).",
            "Ein Register, das verpflichtend wird, schrumpft normalerweise "
            "nicht. Entweder wurde bereinigt, oder die Suche trifft nicht mehr "
            "dasselbe."))

    alt_seite, neu_seite = alt.get("seitengroesse"), neu.get("seitengroesse")
    if alt_seite and neu_seite and neu_seite < alt_seite:
        aenderungen.append(Aenderung(
            "auffaellig", "Seitengröße",
            f"Höchstens {neu_seite} Treffer je Seite statt {alt_seite}.",
            "Dieselbe Trefferliste kostet ab sofort mehr Anfragen. Die "
            "Zeitschätzungen in der Oberfläche sind zu optimistisch."))

    return aenderungen


# ---------------------------------------------------------------------------
# Storage and log
# ---------------------------------------------------------------------------


def letzte_aufnahme(verzeichnis: Path = AUFNAHMEN) -> tuple[Path, dict[str, Any]] | None:
    """The most recent snapshot, or None if there is none yet."""
    dateien = sorted(verzeichnis.glob("aufnahme_*.json"))
    for pfad in reversed(dateien):
        try:
            return pfad, json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Aufnahme %s nicht lesbar: %s", pfad, exc)
    return None


def faellig(verzeichnis: Path = AUFNAHMEN,
            abstand_tage: int = ABSTAND_TAGE) -> tuple[bool, float | None]:
    """Is a new snapshot due? Returns (due, days since the last one)."""
    vorher = letzte_aufnahme(verzeichnis)
    if vorher is None:
        return True, None
    try:
        gemessen = datetime.fromisoformat(vorher[1]["zeitpunkt"])
    except (KeyError, ValueError):
        return True, None
    tage = (datetime.now(timezone.utc) - gemessen).total_seconds() / 86400
    return tage >= abstand_tage, tage


def _protokolliere(aenderungen: list[Aenderung], aufnahme: dict[str, Any],
                   tage: float | None, pfad: Path = PROTOKOLL) -> None:
    """Append a section to the log. "No changes" is recorded too.

    A log holding only changes leaves open whether nothing happened or nobody
    looked -- the same difference as between "not recorded in EUDAMED" and "not
    queried".
    """
    datum = aufnahme["zeitpunkt"][:10]
    if tage is None:
        abstand = "Erstaufnahme"
    else:
        abstand = (f"{tage:.0f} Tag seit der letzten Aufnahme" if round(tage) == 1
                   else f"{tage:.0f} Tage seit der letzten Aufnahme")
    schwer = sum(1 for a in aenderungen if a.schwere == "kritisch")

    # A format change is a finding about this file, not about EUDAMED: nothing
    # was compared. The baseline is then written out again, otherwise the log
    # holds a snapshot whose new checks are recorded nowhere.
    neubeginn = tage is None or any(a.bereich == "Aufnahme" for a in aenderungen)

    kopf = f"## {datum} — "
    if not aufnahme.get("brauchbar", True):
        kopf += "Aufnahme unvollständig, verworfen"
    elif tage is None:
        kopf += "Erstaufnahme"
    elif not aenderungen:
        kopf += "keine Änderungen"
    else:
        kopf += f"{len(aenderungen)} Änderung(en)"
        if schwer:
            kopf += f", davon **{schwer} kritisch**"

    zeilen = ["", kopf, "",
              f"*{abstand}. Datenstand {aufnahme.get('build') or 'unbekannt'}, "
              f"Referenzgruppe {REFERENZ_CND}: {aufnahme.get('basis')} Treffer.*", ""]

    if aufnahme.get("fehler"):
        zeilen += ["Nicht messbar in diesem Lauf:", ""]
        zeilen += [f"- {f}" for f in aufnahme["fehler"]] + [""]

    if not aufnahme.get("brauchbar", True):
        zeilen += [
            f"**{aufnahme.get('mangel')}** — diese Aufnahme wird nicht als "
            f"Bezugspunkt abgelegt und nicht verglichen. Eine halb gemessene "
            f"Schnittstelle als Ausgangszustand wäre schlimmer als keiner: Was "
            f"nie gemessen wurde, kann später nicht als ausgefallen auffallen.",
            "", "Die Aufnahme bleibt fällig und wird beim nächsten Start "
            "wiederholt.", ""]
    elif neubeginn:
        wirkt = [n for n, f in (aufnahme.get("filter") or {}).items()
                 if f["zustand"] == "wirkt"]
        neue = [n for n, w in (aufnahme.get("wunschliste") or {}).items()
                if w["zustand"] == "wirkt"]
        zeilen += [
            "Ausgangszustand festgehalten. Verglichen wird ab der nächsten "
            "Aufnahme.", "",
            f"- Wirksame Filter: {', '.join(f'`{n}`' for n in wirkt) or 'keine'}",
            f"- Geprüfte Codewerte: {len(aufnahme.get('vokabular') or {})}",
            f"- Präfixsuche über `cndCode`: {aufnahme.get('praefixsuche')} "
            f"{aufnahme.get('praefixkette')}",
            f"- Seitenzählung: {aufnahme.get('seitenzaehlung')}, "
            f"höchstens {aufnahme.get('seitengroesse')} Treffer je Seite",
            f"- Schalter der öffentlichen Seite: "
            + ", ".join(f"{n}={'an' if w else 'AUS'}"
                        for n, w in sorted((aufnahme.get("schalter") or {}).items())),
            f"- Unbekannter Parametername wird: {aufnahme.get('kontrolle')} · "
            f"unbekannter Codewert: {aufnahme.get('kanarienvogel')}",
            f"- Aus der Wunschliste wirksam: "
            + (", ".join(f"`{n}`" for n in neue) if neue
               else f"keiner von {len(aufnahme.get('wunschliste') or {})}"),
            f"- Erfasste Feldnamen: "
            + ", ".join(f"{b} ({len(f)})"
                        for b, f in (aufnahme.get("felder") or {}).items()),
            ""]
    elif not aenderungen:
        zeilen += ["Filter, Codewerte, Feldnamen, Präfixsuche, Seitenzählung und "
                   "Wunschliste unverändert.", ""]
    else:
        for a in sorted(aenderungen, key=lambda x: ("kritisch", "auffaellig",
                                                    "hinweis").index(x.schwere)):
            zeilen.append(f"- **{a.schwere}** · {a.bereich}: {a.text}")
            if a.folge:
                zeilen.append(f"  → {a.folge}")
        zeilen.append("")

    kopfzeile = ("# Änderungen an der EUDAMED-Schnittstelle\n\n"
                 "Automatisch geführt von `apiwacht.py`. Neueste Einträge unten.\n"
                 "Was hier steht, ist gemessen — was daraus folgt, entscheidet ein "
                 "Mensch.\n")
    bestand = pfad.read_text(encoding="utf-8") if pfad.is_file() else kopfzeile
    pfad.write_text(bestand + "\n".join(zeilen), encoding="utf-8")


def laufe(client: Any, *, verzeichnis: Path = AUFNAHMEN,
          protokoll: Path = PROTOKOLL, pause_s: float = PAUSE_S,
          melde: Callable[[str], None] | None = None,
          ) -> tuple[dict[str, Any], list[Aenderung]]:
    """One snapshot: measure, compare, store, log.

    An incomplete snapshot does **not** become a baseline. It is stored under a
    different name (`unbrauchbar_*.json`) that `letzte_aufnahme()` does not
    find, the log records what was missing, and the snapshot stays due.
    """
    vorher = letzte_aufnahme(verzeichnis)
    _, tage = faellig(verzeichnis)

    aufnahme = erhebe(client, pause_s=pause_s, melde=melde)
    brauchbar, mangel = vollstaendig(aufnahme)
    aufnahme["brauchbar"] = brauchbar
    if not brauchbar:
        aufnahme["mangel"] = mangel

    aenderungen = (vergleiche(vorher[1], aufnahme)
                   if vorher and brauchbar else [])

    verzeichnis.mkdir(parents=True, exist_ok=True)
    name = "aufnahme" if brauchbar else "unbrauchbar"
    ziel = verzeichnis / f"{name}_{aufnahme['zeitpunkt'][:10]}.json"
    ziel.write_text(json.dumps(aufnahme, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    _protokolliere(aenderungen, aufnahme, tage if vorher else None, protokoll)
    return aufnahme, aenderungen


def starte_im_hintergrund(client_bauen: Callable[[], Any], *,
                          abstand_tage: int = ABSTAND_TAGE,
                          verzoegerung_s: float = VERZOEGERUNG_S,
                          erzwingen: bool = False) -> threading.Thread | None:
    """Run a snapshot in a background thread if one is due. Otherwise a no-op.

    Three guarantees:

    1. **The caller never waits.** Own thread, `daemon=True`.
    2. **A failure stays without consequence** for the calling program.
    3. **Own client.** A shared one is held under its own lock, which this
       would occupy for about a minute.

    Returns the thread, or None if no snapshot was due.
    """
    ist_faellig, tage = faellig(abstand_tage=abstand_tage)
    if not (ist_faellig or erzwingen):
        print(f"API-Wacht: letzte Aufnahme vor {tage:.0f} Tagen, "
              f"nächste in {abstand_tage - tage:.0f} Tagen.")
        return None

    def arbeite() -> None:
        time.sleep(verzoegerung_s)
        try:
            _, aenderungen = laufe(client_bauen())
        except Exception as exc:  # noqa: BLE001 - see guarantee 2
            log.warning("API-Wacht fehlgeschlagen: %s", exc)
            print(f"API-Wacht: Aufnahme fehlgeschlagen ({exc}). "
                  f"Die Recherche ist davon nicht betroffen.")
            return
        kritisch = [a for a in aenderungen if a.schwere == "kritisch"]
        if kritisch:
            print(f"\nAPI-WACHT: {len(kritisch)} KRITISCHE ÄNDERUNG(EN) an der "
                  f"EUDAMED-Schnittstelle:")
            for a in kritisch:
                print(f"  · {a.bereich}: {a.text}")
            print(f"  Einzelheiten in {PROTOKOLL.name}\n")
        else:
            print(f"API-Wacht: Aufnahme fertig, {len(aenderungen)} Änderung(en), "
                  f"keine kritische. Protokoll: {PROTOKOLL.name}")

    grund = "keine frühere Aufnahme" if tage is None else f"letzte vor {tage:.0f} Tagen"
    print(f"API-Wacht: Aufnahme fällig ({grund}) — läuft in {verzoegerung_s:.0f} s "
          f"nebenher, rund 34 Anfragen.")
    thread = threading.Thread(target=arbeite, name="apiwacht", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(WURZEL))
    from client import EudamedClient

    parser = argparse.ArgumentParser(
        description="Nimmt den Zustand der EUDAMED-Schnittstelle auf und "
                    "protokolliert Änderungen in docs/changelog.md.")
    parser.add_argument("--jetzt", action="store_true",
                        help="sofort aufnehmen, auch wenn keine Aufnahme fällig ist")
    args = parser.parse_args()

    ist_faellig, tage = faellig()
    if not (ist_faellig or args.jetzt):
        print(f"Letzte Aufnahme vor {tage:.0f} Tagen, nächste in "
              f"{ABSTAND_TAGE - tage:.0f} Tagen. Erzwingen mit --jetzt.")
        sys.exit(0)
    # Own client without a read cache: measure, do not remember.
    _, aenderungen = laufe(EudamedClient(cache_max_age_s=0), melde=print)
    kritisch = [a for a in aenderungen if a.schwere == "kritisch"]
    print(f"Fertig: {len(aenderungen)} Änderung(en), davon {len(kritisch)} kritisch. "
          f"Protokoll: {PROTOKOLL.relative_to(WURZEL)}")
    sys.exit(2 if kritisch else 0)
