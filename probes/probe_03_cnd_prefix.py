"""Probe 03 — Kann `cndCode` Präfix-Suche?

Frage: Liefert `cndCode=Q01` alle Unterknoten, oder
matcht der Parameter exakt?

Warum das zählt: Die typische Anfrage im Klinikalltag lautet "alle Dentalprodukte",
nicht "alle Produkte mit Code Q010601". Kann die API kein Präfix, müssen wir den
EMDN-Baum lokal expandieren und n Einzelabfragen fahren — bei EUDAMED-Tempo ein
erheblicher Unterschied.

Methode: Trefferzahlen für Codes zunehmender Länge vergleichen. Bei Präfix-Suche
muss gelten: count(Q) >= count(Q01) >= count(Q0106) >= count(Q010601), alle > 0.
Bei exaktem Match sind die kurzen Codes 0 (oder es gibt sie als eigenen Knoten).
"""

from __future__ import annotations

from client import EudamedClient
from probes.base import ProbeResult, Verdict, call

PROBE_ID = "03"
TITLE = "cndCode: Präfix-Suche oder exakter Match?"
QUESTION = "Liefert cndCode=Q01 auch die Unterknoten? Sind mehrere cndCode gleichzeitig möglich?"

#: Von der Wurzel bis zum Blatt. Q010601 = DENTAL ALLOYS (Referenzfall aus Delapro/EUDAMED).
LADDER = ["Q", "Q01", "Q0106", "Q010601"]

#: Zwei Blätter für den Mehrfach-Parameter-Test.
MULTI = ["Q010601", "Q010699"]


def run(client: EudamedClient) -> ProbeResult:
    result = ProbeResult(PROBE_ID, TITLE, QUESTION)
    counts: dict[str, int | None] = {}

    for code in LADDER:
        response, error = call(client.search_devices, cnd_code=code, page=0, page_size=1)
        result.requests_made += 1
        if error or response is None:
            counts[code] = None
            result.add(f"`cndCode={code}` -> Fehler: {error}")
        else:
            counts[code] = response.total_elements
            result.add(f"`cndCode={code}` -> {response.total_elements} Treffer")

    result.data["counts"] = counts

    leaf = counts.get("Q010601")
    mid = counts.get("Q0106")
    top = counts.get("Q01")

    prefix_works = (
        leaf is not None and top is not None and mid is not None
        and top > leaf and mid >= leaf and leaf > 0
    )
    exact_only = leaf is not None and leaf > 0 and top == 0

    # --- Mehrere cndCode gleichzeitig ------------------------------------------
    single_counts = []
    for code in MULTI:
        response, error = call(client.search_devices, cnd_code=code, page=0, page_size=1)
        result.requests_made += 1
        single_counts.append(response.total_elements if response else None)

    multi, err_multi = call(
        client.request,
        "/devices/udiDiData",
        {
            "page": 0, "pageSize": 1, "size": 1,
            "cndCode": MULTI,  # requests serialisiert das als cndCode=A&cndCode=B
            "languageIso2Code": "en",
        },
    )
    result.requests_made += 1

    if err_multi or multi is None:
        result.add(f"Mehrfaches `cndCode` -> Fehler: {err_multi}")
        result.data["multi_cnd"] = None
    else:
        combined = multi.total_elements
        result.add(
            f"`cndCode={MULTI[0]}` = {single_counts[0]}, `cndCode={MULTI[1]}` = {single_counts[1]}, "
            f"beide zusammen = {combined}"
        )
        if combined is not None and single_counts[0] is not None:
            if combined == single_counts[0]:
                result.add("-> Nur der **erste** Wert wirkt; der zweite wird ignoriert.")
            elif combined == single_counts[-1]:
                result.add("-> Nur der **letzte** Wert wirkt.")
            elif combined >= max(c for c in single_counts if c is not None):
                result.add("-> Wirkt wie ein **ODER** über beide Codes. Nutzbar für Gruppenabfragen.")
            else:
                result.add("-> Unerwartetes Verhalten, evtl. UND-Verknüpfung.")
        result.data["multi_cnd"] = {"single": single_counts, "combined": combined}

    if prefix_works:
        result.conclude(
            Verdict.RESOLVED,
            "`cndCode` sucht per Präfix. Gruppenabfragen gehen direkt über den "
            "Elternknoten — kein lokales Expandieren des EMDN-Baums nötig.",
        )
    elif exact_only:
        result.conclude(
            Verdict.RESOLVED,
            "`cndCode` matcht exakt. Für Gruppenabfragen muss der EMDN-Baum lokal "
            "expandiert und je Blattcode eine Abfrage gefahren werden (Phase 3).",
        )
    else:
        result.conclude(
            Verdict.PARTIAL,
            f"Kein klares Muster: {counts}. Manuell nachsehen.",
        )
    return result
