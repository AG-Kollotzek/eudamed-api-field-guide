"""Ask both EUDAMED interfaces the same question in the same time window.

    python scripts/compare_sources.py --srn DE-MF-000006183
    python scripts/compare_sources.py --cnd Q010601
    python scripts/compare_sources.py --srn DE-MF-000006183 --protokoll

Record counts from the two interfaces taken on different days cannot be
compared: EUDAMED receives new registrations daily, so a small gap is a day,
not a disagreement. This probe queries both APIs within one time window and
compares the sets of device UUIDs, separating currency lag from differing
filter semantics.

The two modes carry different meaning:

`--srn` is the clean comparison. `srn` (UI API) and `MF_SRN` (official API)
match the same identifier exactly, so any deviation is currency.

`--cnd` is the semantic comparison and must deviate: `cndCode` matches by
**prefix**, `NOMENCLATURE_CODE` matches **exactly** (and against a stored value
carrying a leading space, see `docs/official-api.md`). For `Q010601` the UI API
therefore also returns `Q01060199` and every deeper leaf.

That yields a directed expectation, testable even though the UI search returns
no EMDN code per hit (31 fields, `cndCode` is not among them):

    official ⊆ UI

A record known only to the UI API is expected — it hangs off a deeper leaf. A
record known only to the official API cannot follow from the semantics: an
exact match never finds what a prefix match misses. That count is the signal.

Costs three to four requests.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client import EudamedClient  # noqa: E402
from client.eudamed_client import DATALAKE_URL, MAX_PAGE_SIZE  # noqa: E402

GRUEN, ROT, GELB, GRAU, AUS = ("\033[32m", "\033[31m", "\033[33m",
                               "\033[90m", "\033[0m")

#: The official API requires both parameters on every call.
OFFIZIELL_FEST = {"format": "json", "api-version": "v1.0"}

#: Above this share of unexplained deviation in the clean comparison (`--srn`)
#: the difference is no longer noise. A couple of registrations arriving
#: between the two requests is normal; five percent is a real lag.
SCHWELLE = 0.05

#: Where the report goes — the same file the API watch records its findings in.
PROTOKOLL = Path(__file__).resolve().parent.parent / "docs" / "changelog.md"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def hole_ui(client: EudamedClient, *, srn: str | None = None,
            cnd: str | None = None, grenze: int = 2000) -> dict[str, str]:
    """UUID -> EMDN code via the UI API. Search results only, no detail calls."""
    # The value stays empty: the UI search returns no EMDN code per hit
    # (31 fields, `cndCode` is not among them). Substituting `basicUdi` would
    # compare a product identifier against a nomenclature code.
    treffer: dict[str, str] = {}
    seite = 0
    while len(treffer) < grenze:
        antwort = client.search_devices(
            cnd_code=cnd, srn=srn, page=seite, page_size=MAX_PAGE_SIZE,
            # Without this the UI search pre-filters to devices on the market,
            # which would be compared against an unfiltered official list.
            device_status=None)
        eintraege = antwort.content
        if not eintraege:
            break
        for e in eintraege:
            uuid = e.get("uuid")
            if uuid:
                treffer[uuid] = ""
        if antwort.is_last_page:
            break
        seite += 1
    return treffer


def hole_offiziell(client: EudamedClient, *, srn: str | None = None,
                   cnd: str | None = None, grenze: int = 2000) -> dict[str, str]:
    """UUID -> EMDN code via the official API, following the cursor.

    `NOMENCLATURE_CODE` is prefixed with a leading space: the stored values
    carry one (`" L031299"`), and without it the query returns zero rows. The
    workaround is applied here so callers do not have to know about it.
    """
    filter: dict[str, Any] = dict(OFFIZIELL_FEST)
    if srn:
        filter["MF_SRN"] = srn
    if cnd:
        filter["NOMENCLATURE_CODE"] = f" {cnd}"

    treffer: dict[str, str] = {}
    pfad, params = "/udi", filter
    while len(treffer) < grenze:
        antwort = client.request(pfad, params, base=DATALAKE_URL)
        daten = antwort.data if isinstance(antwort.data, dict) else {}
        for satz in daten.get("value") or []:
            uuid = satz.get("UUID")
            if uuid:
                treffer[uuid] = str(satz.get("NOMENCLATURE_CODE") or "").strip()
        weiter = daten.get("nextLink")
        if not weiter:
            break
        # `nextLink` comes back as a full URL. The transport joins `base` and
        # path itself, so the rest is stripped here and the opaque `$after`
        # value is passed on as a parameter.
        from urllib.parse import parse_qs, urlparse

        zerlegt = urlparse(weiter)
        pfad = zerlegt.path.split("/eudamed", 1)[-1] or "/udi"
        params = {**filter,
                  **{k: v[0] for k, v in parse_qs(zerlegt.query).items()}}
    return treffer


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def vergleiche(ui: dict[str, str], offiziell: dict[str, str],
               *, praefixlage: bool) -> dict[str, Any]:
    """Compare the two UUID sets. Pure computation, no network.

    `praefixlage` selects the expectation:

      False (`--srn`)  Both sides match the same identifier exactly. Any
                       deviation in EITHER direction is unexplained.
      True  (`--cnd`)  Expected is `official ⊆ UI`. UI-only records are the
                       prefix breadth and thus explained; official-only
                       records cannot follow from the semantics and count.
    """
    nur_ui = set(ui) - set(offiziell)
    nur_off = set(offiziell) - set(ui)
    gemeinsam = set(ui) & set(offiziell)

    ungeklaert = nur_off if praefixlage else (nur_ui | nur_off)
    basis = max(len(ui), len(offiziell)) or 1

    return {
        "ui": len(ui), "offiziell": len(offiziell), "gemeinsam": len(gemeinsam),
        "nur_ui": len(nur_ui), "nur_offiziell": len(nur_off),
        "praefixlage": praefixlage,
        "ungeklaert": len(ungeklaert),
        "ungeklaert_anteil": len(ungeklaert) / basis,
        "beispiele_nur_ui": sorted(nur_ui)[:5],
        "beispiele_nur_offiziell": sorted(nur_off)[:5],
    }


def bericht(b: dict[str, Any], *, filterlage: str, dauer_s: float) -> str:
    """Format the finding as Markdown for terminal and `docs/changelog.md`."""
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    anteil = b["ungeklaert_anteil"]
    urteil = (
        "**Kein messbarer Versatz.** Die Schnittstellen sind sich einig; die "
        "Quellenwahl darf automatisch laufen."
        if anteil == 0 else
        f"**Versatz von {anteil:.1%} ungeklärt.** Unterhalb der Schwelle von "
        f"{SCHWELLE:.0%} — Rauschen aus Neuregistrierungen zwischen den beiden "
        f"Anfragen, die Quellenwahl darf automatisch laufen."
        if anteil < SCHWELLE else
        f"**Versatz von {anteil:.1%} ungeklärt — über der Schwelle von "
        f"{SCHWELLE:.0%}.** Die Betriebsart gehört auf `nur_anreicherung`: "
        f"Die Trefferliste kommt dann immer von der UI-Schnittstelle, die "
        f"offizielle liefert nur die Produktmerkmale nach."
    )
    return "\n".join([
        f"### Quellenvergleich {jetzt}",
        "",
        f"Filterlage: `{filterlage}` · beide Abfragen in {dauer_s:.1f} s",
        "",
        f"| | Datensätze |",
        f"|---|---|",
        f"| UI-Schnittstelle | {b['ui']} |",
        f"| offizielle Schnittstelle | {b['offiziell']} |",
        f"| in beiden | {b['gemeinsam']} |",
        f"| nur UI | {b['nur_ui']}"
        + (" _(erwartet: Präfixbreite)_ |" if b["praefixlage"] else " |"),
        f"| nur offiziell | {b['nur_offiziell']}"
        + (" _(kann nicht aus der Semantik folgen)_ |" if b["praefixlage"]
           else " |"),
        f"| **ungeklärt** | **{b['ungeklaert']}** ({anteil:.1%}) |",
        "",
        urteil,
    ])


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Beide EUDAMED-Schnittstellen dieselbe Frage stellen.")
    gruppe = p.add_mutually_exclusive_group(required=True)
    gruppe.add_argument("--srn", help="Hersteller-SRN — der SAUBERE Vergleich: "
                                      "beide Seiten matchen exakt, Abweichung "
                                      "= Aktualität")
    gruppe.add_argument("--cnd", help="EMDN-Code — der SEMANTISCHE Vergleich: "
                                      "UI sucht mit Präfix, offiziell exakt")
    p.add_argument("--grenze", type=int, default=2000,
                   help="Höchstzahl Datensätze je Seite (Vorgabe 2000)")
    p.add_argument("--protokoll", action="store_true",
                   help=f"Befund an {PROTOKOLL.name} anhängen")
    p.add_argument("--no-cache", action="store_true",
                   help="Zwischenspeicher umgehen — für den Vergleich in "
                        "derselben Minute ist das der Regelfall")
    args = p.parse_args(argv)

    wurzel = Path(__file__).resolve().parent.parent
    client = EudamedClient(raw_cache_dir=wurzel / "raw_cache",
                           use_cache=not args.no_cache)
    filterlage = f"srn={args.srn}" if args.srn else f"cnd={args.cnd}"

    print(f"\n{GRAU}— Quellenvergleich: {filterlage} —{AUS}\n")
    start = datetime.now(timezone.utc)
    try:
        print("  UI-Schnittstelle …", end=" ", flush=True)
        ui = hole_ui(client, srn=args.srn, cnd=args.cnd, grenze=args.grenze)
        print(f"{len(ui)} Datensätze")

        print("  offizielle Schnittstelle …", end=" ", flush=True)
        off = hole_offiziell(client, srn=args.srn, cnd=args.cnd,
                             grenze=args.grenze)
        print(f"{len(off)} Datensätze")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  {ROT}Abruf fehlgeschlagen:{AUS} {exc}", file=sys.stderr)
        return 1

    dauer = (datetime.now(timezone.utc) - start).total_seconds()
    b = vergleiche(ui, off, praefixlage=bool(args.cnd))
    text = bericht(b, filterlage=filterlage, dauer_s=dauer)
    print()
    print("\n".join("  " + z for z in text.splitlines()))

    if b["beispiele_nur_ui"]:
        # In prefix mode these records are explained, not unexplained: they
        # hang off a deeper leaf of the EMDN tree.
        etikett = ("erwartet, Präfixbreite" if b["praefixlage"]
                   else "ungeklärt")
        print(f"\n  {GRAU}Beispiele nur UI ({etikett}):{AUS} "
              + ", ".join(b["beispiele_nur_ui"]))
    if b["beispiele_nur_offiziell"]:
        print(f"  {GRAU}Beispiele nur offiziell:{AUS} "
              + ", ".join(b["beispiele_nur_offiziell"]))

    if args.protokoll:
        with PROTOKOLL.open("a", encoding="utf-8") as f:
            f.write("\n\n" + text + "\n")
        print(f"\n  {GRUEN}Befund angehängt an {PROTOKOLL.name}{AUS}")

    farbe = GRUEN if b["ungeklaert_anteil"] < SCHWELLE else GELB
    print(f"\n{farbe}Ungeklärte Abweichung: {b['ungeklaert_anteil']:.1%}{AUS}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
