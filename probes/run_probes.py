"""Runner für die Probe-Skripte.

    python -m probes.run_probes                  # alle Probes, Bericht nach PROBE_RESULTS.md
    python -m probes.run_probes --only 04        # nur eine
    python -m probes.run_probes --no-cache       # Cache umgehen, frisch abfragen
    python -m probes.run_probes -v               # HTTP-Log mitschreiben

Eine fehlgeschlagene Probe bricht den Lauf nicht ab — bei einer so wackligen API
wäre das nur lästig.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from client import EudamedClient
from probes import (
    probe_01_health,
    probe_02_pagination,
    probe_03_cnd_prefix,
    probe_04_basic_udi,
    probe_05_filters,
    probe_06_certificates,
    probe_07_datalake,
)
from probes.base import ProbeResult, Verdict

PROBES = [
    probe_01_health,
    probe_02_pagination,
    probe_03_cnd_prefix,
    probe_04_basic_udi,
    probe_05_filters,
    probe_06_certificates,
    probe_07_datalake,
]

REPORT_PATH = Path(__file__).resolve().parent.parent / "PROBE_RESULTS.md"

VERDICT_ICON = {
    Verdict.RESOLVED: "✅",
    Verdict.PARTIAL: "🟡",
    Verdict.OPEN: "❌",
    Verdict.ERROR: "💥",
}


def render_report(results: list[ProbeResult], duration_s: float) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Probe-Ergebnisse (Phase 1)",
        "",
        f"Lauf vom {stamp}, Dauer {duration_s:.0f}s, "
        f"{sum(r.requests_made for r in results)} HTTP-Requests.",
        "",
        "Beantwortet die offenen Fragen aus der Sichtung der Referenz-Repos (siehe README).",
        "Rohantworten liegen in `raw_cache/`.",
        "",
        "## Überblick",
        "",
        "| # | Frage | Ergebnis |",
        "|---|---|---|",
    ]
    for result in results:
        icon = VERDICT_ICON.get(result.verdict, "?")
        lines.append(f"| {result.probe_id} | {result.title} | {icon} {result.verdict.value} |")

    lines += ["", "---", ""]

    for result in results:
        icon = VERDICT_ICON.get(result.verdict, "?")
        lines += [
            f"## {icon} Probe {result.probe_id} — {result.title}",
            "",
            f"**Frage:** {result.question}",
            "",
            f"**Ergebnis:** {result.verdict.value} ({result.requests_made} Requests)",
            "",
        ]
        lines += [f"- {finding}" for finding in result.findings]
        if result.data:
            lines += [
                "",
                "<details><summary>Rohdaten</summary>",
                "",
                "```json",
                json.dumps(result.data, indent=2, ensure_ascii=False, default=str)[:12000],
                "```",
                "",
                "</details>",
            ]
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EUDAMED-Probes ausführen")
    parser.add_argument("--only", help="nur diese Probe-ID, z. B. 04")
    parser.add_argument("--no-cache", action="store_true", help="Cache umgehen")
    parser.add_argument("--report", type=Path, default=REPORT_PATH, help="Zielpfad des Berichts")
    parser.add_argument("-v", "--verbose", action="store_true", help="HTTP-Log anzeigen")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    selected = PROBES
    if args.only:
        selected = [p for p in PROBES if p.PROBE_ID == args.only.zfill(2)]
        if not selected:
            print(f"Keine Probe mit ID {args.only!r}. Verfügbar: "
                  f"{', '.join(p.PROBE_ID for p in PROBES)}", file=sys.stderr)
            return 2

    client = EudamedClient(use_cache=not args.no_cache)
    results: list[ProbeResult] = []
    started = time.monotonic()

    for module in selected:
        print(f"\n{'=' * 70}\nProbe {module.PROBE_ID} — {module.TITLE}\n{'=' * 70}", flush=True)
        probe_started = time.monotonic()
        try:
            result = module.run(client)
        except Exception as exc:  # noqa: BLE001 - ein Absturz darf den Lauf nicht beenden
            result = ProbeResult(module.PROBE_ID, module.TITLE, module.QUESTION)
            result.conclude(Verdict.ERROR, f"Probe abgestürzt: {type(exc).__name__}: {exc}")
        results.append(result)

        for finding in result.findings:
            print(f"  {finding}")
        print(f"  -> {VERDICT_ICON.get(result.verdict, '?')} {result.verdict.value} "
              f"({time.monotonic() - probe_started:.0f}s, {result.requests_made} Requests)", flush=True)

    duration = time.monotonic() - started
    args.report.write_text(render_report(results, duration), encoding="utf-8")

    print(f"\n{'=' * 70}")
    for result in results:
        print(f"  {VERDICT_ICON.get(result.verdict, '?')} Probe {result.probe_id}: {result.verdict.value}")
    print(f"\nBericht: {args.report}")

    return 0 if all(r.verdict != Verdict.ERROR for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
