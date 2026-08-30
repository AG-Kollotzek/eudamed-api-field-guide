"""Render the certificate census: EUDAMED's mandatory-phase fill level.

    python scripts/fill_level.py [--server-jsonl PATH] [--aufnahmen GLOBDIR]

Reads every measurement series available — the seed anchors and the daily
census lines in data/census/, optionally a freshly fetched server JSONL and
the watch's aufnahme_*.json snapshots — deduplicates by date, and writes
docs/certificate-census.md plus the SVG curve it embeds.

Tolerant by design: snapshot formats 2–5 are read for their stable count
fields only (`zeitpunkt`, `gesamt`, `bestaende`), never compared by format
version. A gap in the series is reported, not hidden.
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import date, datetime
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
CENSUS = WURZEL / "data" / "census"
DOCS = WURZEL / "docs"

#: Regulatory window drawn on the chart. Verify against Regulation (EU)
#: 2024/1860 and the implementing decision before publishing conclusions.
PFLICHT_START = date(2026, 5, 28)
NACHTRAG_ENDE = date(2027, 5, 28)

#: A hole in the daily series longer than this is listed explicitly.
LUECKE_TAGE = 3


def _zeilen(pfad: Path) -> list[dict]:
    if not pfad.exists():
        return []
    saetze = []
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        try:
            saetze.append(json.loads(zeile))
        except json.JSONDecodeError:
            continue
    return saetze


def _aus_jsonl(satz: dict, quelle: str) -> dict | None:
    """One census point from a daily line or a seed line."""
    if satz.get("status") not in (None, "ok"):
        return None                       # error lines mark outages, not counts
    live = satz.get("live") or {}
    if "zertifikate" not in live or not satz.get("datum"):
        return None
    lokal = satz.get("lokal") or {}
    # The MDR/IVDR split comes from the local weekly sync and carries its own
    # `stand`. Show it only when that sync is close to the measurement date —
    # a July split next to an August total would silently mislabel the total.
    stand = str(lokal.get("stand") or satz["datum"])[:10]
    frisch = abs(date.fromisoformat(satz["datum"]).toordinal()
                 - date.fromisoformat(stand).toordinal()) <= 7
    return {"datum": satz["datum"], "zertifikate": live["zertifikate"],
            "geraete": live.get("geraete"),
            "mdr": lokal.get("mdr") if frisch else None,
            "ivdr": lokal.get("ivdr") if frisch else None,
            "abgelehnte": (satz.get("wochenmodule") or {}).get("abgelehnte"),
            "quelle": satz.get("quelle") or quelle}


def _aus_aufnahme(pfad: Path) -> dict | None:
    """One point from a watch snapshot — stable count fields only."""
    try:
        satz = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    zeit = satz.get("zeitpunkt")
    zert = (satz.get("bestaende") or {}).get("zertifikate")
    if not zeit or zert is None:          # formats < 5 carry no certificate count
        return None
    return {"datum": zeit[:10], "zertifikate": zert,
            "geraete": satz.get("gesamt"), "mdr": None, "ivdr": None,
            "abgelehnte": None, "quelle": "watch"}


def sammle(server_jsonl: Path | None, aufnahmen_dirs: list[Path]) -> list[dict]:
    punkte: dict[str, dict] = {}
    quellen = [(CENSUS / "seed.jsonl", "documented"),
               (CENSUS / "bestand.jsonl", "census")]
    if server_jsonl:
        quellen.append((server_jsonl, "census"))
    for pfad, art in quellen:
        for satz in _zeilen(pfad):
            punkt = _aus_jsonl(satz, art)
            if punkt:
                # measured beats documented on the same date
                alt = punkte.get(punkt["datum"])
                if not alt or alt["quelle"] == "documented":
                    punkte[punkt["datum"]] = punkt
    for verzeichnis in aufnahmen_dirs:
        for pfad in sorted(glob.glob(str(verzeichnis / "aufnahme_*.json"))):
            punkt = _aus_aufnahme(Path(pfad))
            if punkt and punkt["datum"] not in punkte:
                punkte[punkt["datum"]] = punkt
    return sorted(punkte.values(), key=lambda p: p["datum"])


def luecken(punkte: list[dict]) -> list[str]:
    """Holes between census points — from the first *daily* point onward.

    The documented anchors predate the daily cron; distances between them are
    history, not measurement gaps.
    """
    taeglich = [p for p in punkte if p["quelle"] == "census"]
    meldungen = []
    for a, b in zip(taeglich, taeglich[1:]):
        d1 = date.fromisoformat(a["datum"])
        d2 = date.fromisoformat(b["datum"])
        if (d2 - d1).days > LUECKE_TAGE:
            meldungen.append(f"{d1} → {d2} ({(d2 - d1).days} days)")
    return meldungen


def svg(punkte: list[dict]) -> str:
    """The fill curve, plain SVG, no dependencies. Time axis fixed to the
    regulatory window so the empty right-hand side stays visible: the point
    of the chart is how much of the window is still ahead."""
    breite, hoehe, rand = 900, 380, 60
    t0 = PFLICHT_START.toordinal() - 14
    t1 = NACHTRAG_ENDE.toordinal() + 14
    werte = [p["zertifikate"] for p in punkte]
    y_max = max(werte) * 1.3 if werte else 10000

    def x(d: date) -> float:
        return rand + (d.toordinal() - t0) / (t1 - t0) * (breite - 2 * rand)

    def y(wert: float) -> float:
        return hoehe - rand - wert / y_max * (hoehe - 2 * rand)

    teile = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {breite} {hoehe}" '
             f'font-family="system-ui, sans-serif" font-size="12">',
             f'<rect width="{breite}" height="{hoehe}" fill="white"/>']
    # regulatory markers
    for tag, name in ((PFLICHT_START, "mandatory since 2026-05-28"),
                      (NACHTRAG_ENDE, "NB backfill deadline 2027-05-28")):
        teile.append(f'<line x1="{x(tag):.1f}" y1="{rand}" x2="{x(tag):.1f}" '
                     f'y2="{hoehe - rand}" stroke="#b91c1c" stroke-dasharray="4 3"/>')
        teile.append(f'<text x="{x(tag) + 4:.1f}" y="{rand + 12}" '
                     f'fill="#b91c1c">{name}</text>')
    # axes
    teile.append(f'<line x1="{rand}" y1="{hoehe - rand}" x2="{breite - rand}" '
                 f'y2="{hoehe - rand}" stroke="#333"/>')
    teile.append(f'<line x1="{rand}" y1="{rand}" x2="{rand}" '
                 f'y2="{hoehe - rand}" stroke="#333"/>')
    for stufe in range(0, int(y_max) + 1, max(1000, int(y_max // 5 // 1000 * 1000) or 1000)):
        teile.append(f'<text x="{rand - 8}" y="{y(stufe) + 4:.1f}" '
                     f'text-anchor="end" fill="#555">{stufe:,}</text>')
        teile.append(f'<line x1="{rand}" y1="{y(stufe):.1f}" x2="{breite - rand}" '
                     f'y2="{y(stufe):.1f}" stroke="#eee"/>')
    monat = date(2026, 6, 1)
    while monat <= date(2027, 6, 1):
        teile.append(f'<text x="{x(monat):.1f}" y="{hoehe - rand + 18}" '
                     f'text-anchor="middle" fill="#555">{monat.strftime("%b %y")}</text>')
        monat = date(monat.year + (monat.month == 12), monat.month % 12 + 1, 1)
    # curve + points
    koordinaten = [(x(date.fromisoformat(p["datum"])), y(p["zertifikate"]), p)
                   for p in punkte]
    if len(koordinaten) > 1:
        pfad = " ".join(f"{px:.1f},{py:.1f}" for px, py, _ in koordinaten)
        teile.append(f'<polyline points="{pfad}" fill="none" stroke="#1d4ed8" '
                     f'stroke-width="2"/>')
    for px, py, p in koordinaten:
        if p["quelle"] == "documented":
            teile.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="white" '
                         f'stroke="#1d4ed8" stroke-width="2"/>')
        else:
            teile.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="#1d4ed8"/>')
    teile.append(f'<text x="{rand}" y="{rand - 30}" font-size="16" fill="#111">'
                 f'EUDAMED certificate-search module: total records</text>')
    teile.append(f'<text x="{rand}" y="{rand - 12}" fill="#555">open circles = '
                 f'documented anchors · filled = daily census</text>')
    teile.append("</svg>")
    return "\n".join(teile)


def markdown(punkte: list[dict], loecher: list[str]) -> str:
    zeilen = ["| Date | Certificates | MDR | IVDR | Refused | Source |",
              "|---|---|---|---|---|---|"]
    for p in punkte:
        zeilen.append(
            f"| {p['datum']} | {p['zertifikate']:,} "
            f"| {p['mdr'] if p['mdr'] is not None else '—'} "
            f"| {p['ivdr'] if p['ivdr'] is not None else '—'} "
            f"| {p['abgelehnte'] if p['abgelehnte'] is not None else '—'} "
            f"| {p['quelle']} |")
    tabelle = "\n".join(zeilen)
    lueckentext = ("None so far." if not loecher else
                   "\n".join(f"- {l}" for l in loecher))
    stand = punkte[-1]["datum"] if punkte else "—"
    return f"""# The certificate census

How fast is EUDAMED's certificate module filling up? Since **2026-05-28**
the first EUDAMED modules are mandatory, and notified bodies may backfill
legacy certificates until **2027-05-28**. This page tracks the fill level —
one measurement per day, one dot per day.

![Certificate fill curve](certificate-census.svg)

## Method — what exactly is counted

**This is not scraping.** Every paginated response of the UI API carries
`totalElements`, the server-side count of the full result set. The census
asks the counting machine three questions a day (devices, certificates, one
reference group) plus feature flags and build version — seven requests, 2 s
apart, appended as one JSON line. The line schema is frozen; fields are only
ever added. Failed runs write an error line: outages are availability data,
not gaps.

**What the curve counts:** the **certificate-search module**
(`api/certificates/search/`), which stores MDR/IVDR certificates linked to
the manufacturer via `actorSrn`. It does **not** count "all certificates in
EUDAMED": the device-linked legacy store (`deviceCertificateInfoList` inside
Basic-UDI records, almost exclusively MDD/AIMDD, ~544 records) is a disjoint
data path with no cheap count — see
[gotcha 8](gotchas.md#8-the-two-certificate-sources-are-disjoint). The
MDR/IVDR split comes from a weekly full pull (14 pages), which is also
archived as a dated dump — that archive is what enables per-notified-body
fill rates, status transitions and upload-lag analysis (new UUIDs per week
against their `issueDate`).

**Known limitation:** the device-linked legacy path is not measured
longitudinally. A fixed monthly device panel could close that; it is noted,
not built.

Refused applications are counted weekly from `api/applications/search/`
(measured 2026-08-30 with a control probe: 290 refused vs 4,654 issued —
the endpoint, not a parameter, separates the two).

## The series

{tabelle}

Measurement gaps longer than {LUECKE_TAGE} days in the daily series:

{lueckentext}

## Reproduce

```bash
# one census point (in the AskEUDAMED tool repo; 7 requests)
python scripts/zaehlstand.py

# re-render this page from the checked-in series
python scripts/fill_level.py
```

Raw series: [`data/census/`](../data/census/) — seed anchors with their
provenance, the daily JSONL, and dated weekly dumps of the full certificate
table.

*Last data point: {stand}. Deadline dates should be re-verified against
Regulation (EU) 2024/1860 and the implementing decision before citing.*
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-jsonl", type=Path, default=None,
                        help="freshly fetched bestand.jsonl from the server")
    parser.add_argument("--aufnahmen", type=Path, nargs="*",
                        default=[WURZEL / "output" / "apiwacht"],
                        help="directories with watch aufnahme_*.json files")
    args = parser.parse_args()

    punkte = sammle(args.server_jsonl, list(args.aufnahmen))
    if not punkte:
        print("No census points found — nothing to render.")
        return 1
    loecher = luecken(punkte)
    (DOCS / "certificate-census.svg").write_text(svg(punkte), encoding="utf-8")
    (DOCS / "certificate-census.md").write_text(markdown(punkte, loecher),
                                                encoding="utf-8")
    print(f"{len(punkte)} points → docs/certificate-census.md + .svg"
          + (f" · gaps: {len(loecher)}" if loecher else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
