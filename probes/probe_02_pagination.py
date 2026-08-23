"""Probe 02 — Paginierungsmechanik.

Fragen:
  a) Ist `page` 0- oder 1-basiert? (Die Repos widersprechen sich.)
  b) Ist `size` wirklich bei 300 gedeckelt, oder geht mehr?
  c) Liefert `size=1` zuverlässig `totalElements`? (Grundlage von count_devices)

Methode für (a): Seite 0 und Seite 1 abrufen und die Treffer vergleichen. Wären
sie 1-basiert, müsste `page=0` entweder fehlschlagen oder dasselbe liefern wie `page=1`.
"""

from __future__ import annotations

from client import EudamedClient
from probes.base import ProbeResult, Verdict, call

PROBE_ID = "02"
TITLE = "Paginierung: 0- oder 1-basiert, Größenlimit"
QUESTION = "Wie verhalten sich page/pageSize/size wirklich?"

#: Dentallegierungen — der von Delapro/EUDAMED verifizierte Referenzfall, groß genug für mehrere Seiten.
REFERENCE_CND = "Q010601"


def run(client: EudamedClient) -> ProbeResult:
    result = ProbeResult(PROBE_ID, TITLE, QUESTION)

    # --- (a) 0- oder 1-basiert? -------------------------------------------------
    page0, err0 = call(client.search_devices, cnd_code=REFERENCE_CND, page=0, page_size=5)
    page1, err1 = call(client.search_devices, cnd_code=REFERENCE_CND, page=1, page_size=5)
    result.requests_made += 2

    if err0 or page0 is None:
        result.conclude(Verdict.ERROR, f"Seite 0 nicht abrufbar: {err0}")
        return result

    total = page0.total_elements
    result.add(f"Referenzabfrage `cndCode={REFERENCE_CND}`: {total} Treffer gesamt.")
    result.add(f"`page=0` -> Antwortfeld `number` = `{page0.data.get('number')}`, "
               f"{len(page0.content)} Treffer.")
    result.data["page0_uuids"] = [e.get("uuid") for e in page0.content]

    if err1 or page1 is None:
        result.add(f"`page=1` fehlgeschlagen: {err1}")
    else:
        result.add(f"`page=1` -> Antwortfeld `number` = `{page1.data.get('number')}`, "
                   f"{len(page1.content)} Treffer.")
        result.data["page1_uuids"] = [e.get("uuid") for e in page1.content]

        uuids0 = {e.get("uuid") for e in page0.content}
        uuids1 = {e.get("uuid") for e in page1.content}
        overlap = uuids0 & uuids1

        if page0.data.get("number") == 0 and not overlap:
            result.add("Seite 0 und Seite 1 überschneiden sich nicht, und Seite 0 meldet `number=0`.")
            zero_based = True
        elif overlap == uuids0:
            result.add("⚠️ Seite 0 und Seite 1 liefern dieselben Treffer — spricht für 1-basiert.")
            zero_based = False
        else:
            result.add(f"⚠️ Unklar: {len(overlap)} überlappende Treffer.")
            zero_based = None
        result.data["zero_based"] = zero_based

    # --- (b) Größenlimit --------------------------------------------------------
    for requested in (300, 500):
        response, error = call(
            client.search_devices,
            cnd_code=REFERENCE_CND, page=0, page_size=requested,
        )
        result.requests_made += 1
        if error or response is None:
            result.add(f"`size={requested}` -> Fehler: {error}")
            continue
        returned = response.data.get("numberOfElements")
        reported = response.data.get("size")
        result.add(
            f"`size={requested}` angefragt -> Antwort meldet `size={reported}`, "
            f"`numberOfElements={returned}`."
        )
        result.data[f"size_{requested}"] = {"size": reported, "numberOfElements": returned}

    # Hinweis: search_devices deckelt selbst bei MAX_PAGE_SIZE. Für den 500er-Test
    # deshalb einmal am Deckel vorbei, um das echte Serververhalten zu sehen.
    raw500, err500 = call(
        client.request,
        "/devices/udiDiData",
        {
            "page": 0, "pageSize": 500, "size": 500,
            "cndCode": REFERENCE_CND, "languageIso2Code": "en",
        },
    )
    result.requests_made += 1
    if err500 or raw500 is None:
        result.add(f"Ungedeckelt `size=500` -> Fehler: {err500} (spricht für ein hartes Limit)")
    else:
        result.add(
            f"Ungedeckelt `size=500` -> Antwort meldet `size={raw500.data.get('size')}`, "
            f"`numberOfElements={raw500.data.get('numberOfElements')}`."
        )
        result.data["size_500_uncapped"] = {
            "size": raw500.data.get("size"),
            "numberOfElements": raw500.data.get("numberOfElements"),
        }

    # --- (c) size=1 als Zähltrick ----------------------------------------------
    counter, err_counter = call(client.search_devices, cnd_code=REFERENCE_CND, page=0, page_size=1)
    result.requests_made += 1
    if err_counter or counter is None:
        result.add(f"`size=1` fehlgeschlagen: {err_counter} — `count_devices()` wäre unbrauchbar.")
    else:
        matches = counter.total_elements == total
        result.add(
            f"`size=1` -> `totalElements={counter.total_elements}` "
            f"({'identisch mit' if matches else '⚠️ abweichend von'} der Vollabfrage)."
        )
        result.data["count_trick_consistent"] = matches

    zero_based = result.data.get("zero_based")
    if zero_based is True:
        result.conclude(
            Verdict.RESOLVED,
            "`page` ist 0-basiert — die openregulatory-Spec dokumentiert 'starts at 1', gemessen beginnt sie bei 0. "
            "Größenverhalten siehe oben.",
        )
    elif zero_based is False:
        result.conclude(
            Verdict.RESOLVED,
            "⚠️ `page` verhält sich 1-basiert — Doku und Client korrigieren!",
        )
    else:
        result.conclude(Verdict.PARTIAL, "Basis der Seitenzählung nicht eindeutig bestimmbar.")
    return result
