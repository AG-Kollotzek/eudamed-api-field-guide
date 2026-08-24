"""Probe 03 — does `cndCode` search by prefix?

Question: does `cndCode=Q01` also return the child nodes, or does the
parameter match exactly? Without prefix matching, a query over a whole EMDN
branch requires expanding the tree locally and one request per leaf code.

Method: compare hit counts for codes of increasing length. Prefix matching
implies count(Q) >= count(Q01) >= count(Q0106) >= count(Q010601), all > 0.
With exact matching the short codes return 0 unless they exist as nodes of
their own.
"""

from __future__ import annotations

from client import EudamedClient
from probes.base import ProbeResult, Verdict, call

PROBE_ID = "03"
TITLE = "cndCode: Präfix-Suche oder exakter Match?"
QUESTION = "Liefert cndCode=Q01 auch die Unterknoten? Sind mehrere cndCode gleichzeitig möglich?"

#: Root to leaf. Q010601 = DENTAL ALLOYS, used as the reference code.
LADDER = ["Q", "Q01", "Q0106", "Q010601"]

#: Two leaf codes for the repeated-parameter test.
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

    # --- Several cndCode values at once -----------------------------------------
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
            "cndCode": MULTI,  # requests serialises this as cndCode=A&cndCode=B
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
