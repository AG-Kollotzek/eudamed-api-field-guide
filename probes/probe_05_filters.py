"""Probe 05 — which undocumented filter parameters actually take effect?

Question: the EUDAMED UI visibly offers filters for risk class, legislation
and date range, but no public source names the parameters behind them. Which
of the obvious candidate names work?

Method: compare the hit count of a baseline query with the hit count of the
same query plus one candidate parameter.

  - count changes     -> the parameter takes effect.
  - count unchanged   -> the parameter is silently ignored (the normal case for
                         Spring-based APIs) OR the filter happens to match
                         everything. Values are therefore chosen so that they
                         must exclude something.
  - HTTP error        -> the parameter is actively rejected; also a result.

"Takes effect" here means "changes the result set plausibly", not "filters
demonstrably correctly". Every hit needs a content check before it is relied
on.
"""

from __future__ import annotations

from typing import Any

from client import EudamedClient
from probes.base import ProbeResult, Verdict, call

PROBE_ID = "05"
TITLE = "Undokumentierte Filterparameter"
QUESTION = "Wirken riskClassCode, Legislation-, Datums- und NB-Filter auf /devices/udiDiData?"

REFERENCE_CND = "Q010601"

#: (parameter name, value, intended effect)
CANDIDATES: list[tuple[str, Any, str]] = [
    ("riskClassCode", "refdata.risk-class.class-iia", "Risikoklasse IIa"),
    ("riskClass", "refdata.risk-class.class-iia", "Risikoklasse, alternativer Name"),
    ("applicableLegislationCode", "refdata.applicable-legislation.mdr", "nur MDR"),
    ("legislationCode", "refdata.applicable-legislation.mdr", "Legislation, alternativer Name"),
    ("notifiedBodySrn", "0197", "nur Geräte mit TÜV-Rheinland-Zertifikat"),
    ("countryIso2Code", "DE", "nur in DE verfügbar"),
    ("issueDateFrom", "2023-01-01", "Ausstellungsdatum ab"),
    ("expiryDateFrom", "2026-01-01", "Ablaufdatum ab"),
    ("certificateExpiryFrom", "2026-01-01", "Ablaufdatum ab, alternativer Name"),
    ("versionDateFrom", "2023-01-01", "Datensatz-Version ab"),
]


def run(client: EudamedClient) -> ProbeResult:
    result = ProbeResult(PROBE_ID, TITLE, QUESTION)

    baseline, error = call(client.search_devices, cnd_code=REFERENCE_CND, page=0, page_size=1)
    result.requests_made += 1
    if error or baseline is None:
        result.conclude(Verdict.ERROR, f"Basisabfrage fehlgeschlagen: {error}")
        return result

    base_count = baseline.total_elements
    result.add(f"Basis: `cndCode={REFERENCE_CND}` -> **{base_count}** Treffer.")

    # Control: a parameter that certainly does not exist. If it leaves the count
    # unchanged, unknown parameters are confirmed to be silently ignored — only
    # then is "count unchanged" interpretable at all.
    control, err_control = call(
        client.search_devices,
        cnd_code=REFERENCE_CND, page=0, page_size=1,
        extra_params={"diesenParameterGibtEsNicht": "xyz123"},
    )
    result.requests_made += 1
    if err_control or control is None:
        result.add(f"Kontrollprobe mit Fantasieparameter -> Fehler: {err_control} "
                   "(die API lehnt Unbekanntes also ab — gute Nachricht für die Aussagekraft)")
        ignores_unknown = False
    else:
        ignores_unknown = control.total_elements == base_count
        result.add(
            f"Kontrollprobe mit Fantasieparameter -> {control.total_elements} Treffer "
            f"({'unverändert -> unbekannte Parameter werden ignoriert' if ignores_unknown else 'verändert (!)'})"
        )
    result.data["ignores_unknown_params"] = ignores_unknown

    findings: dict[str, dict[str, Any]] = {}
    working: list[str] = []

    for name, value, purpose in CANDIDATES:
        response, err = call(
            client.search_devices,
            cnd_code=REFERENCE_CND, page=0, page_size=1,
            extra_params={name: value},
        )
        result.requests_made += 1

        if err or response is None:
            findings[name] = {"status": "abgelehnt", "detail": err}
            result.add(f"`{name}={value}` ({purpose}) -> ❌ abgelehnt: {err}")
            continue

        count = response.total_elements
        if count == base_count:
            findings[name] = {"status": "ignoriert", "count": count}
            result.add(f"`{name}={value}` ({purpose}) -> {count} Treffer — unverändert, wirkt nicht.")
        else:
            findings[name] = {"status": "wirkt", "count": count}
            working.append(name)
            result.add(f"`{name}={value}` ({purpose}) -> ✅ **{count}** Treffer (von {base_count}) — wirkt.")

    result.data["findings"] = findings
    result.data["working"] = working

    if not ignores_unknown and not working:
        result.conclude(
            Verdict.PARTIAL,
            "Kontrollprobe nicht eindeutig — die Aussage 'unverändert = wirkungslos' "
            "trägt hier nicht. Ergebnisse manuell prüfen.",
        )
    elif working:
        result.conclude(
            Verdict.RESOLVED,
            f"Serverseitig wirksam: `{'`, `'.join(working)}`. Diese in "
            "Ein Client sollte ihn serverseitig nutzen statt lokal nachzufiltern. "
            "Alle übrigen bleiben clientseitige Filter.",
        )
    else:
        result.conclude(
            Verdict.RESOLVED,
            "Keiner der geratenen Parameter wirkt. Datum, Risikoklasse und Legislation "
            "bleiben **clientseitige** Filter nach dem Abruf — so wie in "
            "vorgesehen. Wer sie serverseitig will, muss den "
            "UI-Traffic mitschneiden.",
        )
    return result
