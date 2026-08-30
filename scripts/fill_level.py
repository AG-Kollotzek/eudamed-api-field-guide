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
from datetime import date, datetime, timezone
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

#: If the newest point is older than this, the page says so in a warning
#: block. Without it a dead cron looks exactly like a healthy series: the
#: page keeps showing its last value, formally correct and factually stale.
VERALTET_TAGE = 10


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
            # None on documented anchors and on lines from before the control
            # counts existed — only False means a measured disagreement.
            "stimmig": (satz.get("kontrolle") or {}).get("stimmig"),
            "nb_summe": (satz.get("nb_verteilung") or {}).get("summe"),
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
            "abgelehnte": None, "stimmig": None, "nb_summe": None,
            "quelle": "watch"}


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


def _warnblock(punkte: list[dict], heute: date) -> str:
    """A visible staleness banner. A dead cron and a healthy series look
    identical on a page that just shows its last value."""
    taeglich = [p for p in punkte if p["quelle"] == "census"]
    if not taeglich:
        return ("> ⚠️ **No census measurement yet.** Every point below is a "
                "documented anchor; the daily series has not started.\n\n")
    # A disagreeing control count matters more than a stale one: it means the
    # number on the chart may no longer mean what earlier points meant.
    uneins = [p for p in taeglich if p.get("stimmig") is False]
    schief = [p for p in taeglich
              if p.get("nb_summe") is not None
              and p["nb_summe"] != p["zertifikate"]]
    warnung = ""
    if uneins:
        warnung += (
            f"> ⚠️ **The control counts disagree** on "
            f"{len(uneins)} measurement(s), most recently "
            f"{uneins[-1]['datum']}. The same query counted differently when "
            f"stated explicitly, which means the default may have changed "
            f"meaning. Treat any step in the curve around that date as a "
            f"measurement artefact until it is explained.\n\n")
    if schief:
        p_ = schief[-1]
        warnung += (
            f"> ⚠️ **The per-notified-body sum no longer matches the total** "
            f"({p_['nb_summe']:,} vs {p_['zertifikate']:,} on {p_['datum']}). "
            f"The partition was exact when first measured, so a difference is "
            f"itself a finding — a certificate without a notified body, a "
            f"body missing from the list, or double counting.\n\n")
    if warnung:
        return warnung
    alter = (heute - date.fromisoformat(taeglich[-1]["datum"])).days
    if alter > VERALTET_TAGE:
        return (f"> ⚠️ **This series is stale.** The newest measurement is "
                f"{alter} days old ({taeglich[-1]['datum']}), which is longer "
                f"than the {VERALTET_TAGE}-day threshold — the collector has "
                f"most likely stopped. Do not read the last value as "
                f"current.\n\n")
    return ""


def _lueckenblock(punkte: list[dict], loecher: list[str]) -> str:
    taeglich = [p for p in punkte if p["quelle"] == "census"]
    if len(taeglich) < 2:
        return (f"Gap detection needs at least two daily measurements; there "
                f"{'is' if len(taeglich) == 1 else 'are'} {len(taeglich)} so "
                f"far. It is therefore not yet meaningful to say the series "
                f"has no gaps.")
    if not loecher:
        return (f"No gaps longer than {LUECKE_TAGE} days across "
                f"{len(taeglich)} daily measurements.")
    return (f"Measurement gaps longer than {LUECKE_TAGE} days:\n\n"
            + "\n".join(f"- {l}" for l in loecher))


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
    heute = date.fromisoformat(
        datetime.now(timezone.utc).date().isoformat())
    warnblock = _warnblock(punkte, heute)
    lueckenblock = _lueckenblock(punkte, loecher)
    gemessen = len([p for p in punkte if p["quelle"] == "census"])
    punktsatz = (f"So far the series holds **{gemessen} measured "
                 f"{'point' if gemessen == 1 else 'points'}** and "
                 f"{len(punkte) - gemessen} documented anchors.")
    stand = punkte[-1]["datum"] if punkte else "—"
    return f"""# The certificate census

How fast is EUDAMED's certificate module filling up? Since **2026-05-28**
the first EUDAMED modules are mandatory, and notified bodies may backfill
legacy certificates until **2027-05-28**. This page tracks the fill level.

{warnblock}![Certificate fill curve](certificate-census.svg)

**Cadence:** a cron measures once a day (Mon–Sat) and once on Sunday with a
full-table dump. Documented anchors predate that cron and are marked as such
in the table. {punktsatz}

## Method — what exactly is counted

**This is not scraping.** Every paginated response of the UI API carries
`totalElements`, the server-side count of the full result set. The census
asks the counting machine a handful of questions a day — devices,
certificates, three radiation-oncology reference groups, plus feature flags
and build version — seven requests, 2 s apart, appended as one JSON line.
The line schema is frozen; fields are only ever added. Failed runs write an
error line: outages are availability data, not gaps.

**Exactly what the number is.** It is `totalElements` of
`api/certificates/search/` with no filter: **certificate records in the
manufacturer-level search module, in every status**. Measured against the
2026-07-30 full pull, that population breaks down as issued 2,568,
supplemented 832, amended 328, reissued 141, withdrawn 76, cancelled 60,
and a remainder of suspended/restricted. It counts *records*, not distinct
certificates: 4,055 rows carried 3,958 distinct `certificate_number` values,
so roughly 2.4% are versions of a number that also appears elsewhere.
Anyone comparing this curve against "certificates issued" from a survey is
comparing two different quantities.

**What it is not.** It does not count "all certificates in EUDAMED". The
device-linked legacy store (`deviceCertificateInfoList` inside Basic-UDI
records, almost exclusively MDD/AIMDD) is a disjoint data path with no cheap
count — see
[gotcha 8](gotchas.md#8-the-two-certificate-sources-are-disjoint). That path
is not measured longitudinally here; a fixed monthly device panel could
close the gap, but none is running.

**What the weekly dumps will support — and do not yet.** A dated dump of the
full certificate table is archived every week before the next sync
overwrites it. The **difference between two such dumps** yields per-notified-
body fill rates, status transitions, and upload lag (UUIDs new in a given
week, against their `issueDate`). None of that is computable from a single
dump: within one pull, `first_seen_at` is the day of that pull for every
row, not the day the record appeared in EUDAMED. At the time of writing one
dump exists, so these analyses are pending, not available.

Refused applications are counted weekly from `api/applications/search/`
(measured 2026-08-30 with a control probe: 290 refused against 4,654 in the
issued-side module — the endpoint separates the two, not a parameter).

## The series

{tabelle}

{lueckenblock}

## Reproduce

```bash
# one census point (in the AskEUDAMED tool repo; 7 requests)
python scripts/zaehlstand.py

# re-render this page from the checked-in series
python scripts/fill_level.py
```

Raw series:
[`data/census/`](https://github.com/AG-Kollotzek/eudamed-api-field-guide/tree/main/data/census)
— seed anchors with their provenance, the daily JSONL, and dated dumps of the
certificate table. (Full URL on purpose: only `docs/` is published as a
website, so a relative link out of it would not resolve.)

## Source and licence of the underlying data

The measured values and the archived dumps are derived from EUDAMED, a
database of the European Commission. Commission content is reusable under
Decision 2011/833/EU, and the Commission's legal notice places it under
**CC BY 4.0** unless stated otherwise — which also covers the *sui generis*
database right (CC BY 4.0 §4). Attribution is the condition, so:

> Contains data from EUDAMED, © European Union, 1995–2026, reused under
> CC BY 4.0. The European Commission is not responsible for any use made of
> this material, and this page is not endorsed by it.

The published dumps deliberately omit `actor_name` and `actor_srn`: among
manufacturers registered as sole traders those fields carry the names of
natural persons, and none of the intended analyses need them.

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
