"""Probe 01 — availability and build version.

Question: does the API respond, and which EUDAMED build is running?
`/applicationInfo` reports `buildVersion`, `lastBuildDate` and `activeProfile`.
The build version belongs in every record, because field names have changed
between EUDAMED releases.
"""

from __future__ import annotations

from client import EudamedClient
from probes.base import ProbeResult, Verdict, call

PROBE_ID = "01"
TITLE = "Erreichbarkeit und Versionsstand"
QUESTION = "Antwortet die API, und welcher EUDAMED-Build läuft gerade?"


def run(client: EudamedClient) -> ProbeResult:
    result = ProbeResult(PROBE_ID, TITLE, QUESTION)

    response, error = call(client.get_application_info)
    result.requests_made += 1

    if error or response is None:
        result.conclude(Verdict.ERROR, f"/applicationInfo nicht erreichbar: {error}")
        return result

    info = response.data if isinstance(response.data, dict) else {}
    result.data["applicationInfo"] = info
    result.add(f"Antwortzeit: {response.elapsed_s:.2f}s")
    for key in ("buildVersion", "lastBuildDate", "activeProfile"):
        if key in info:
            result.add(f"`{key}`: `{info[key]}`")

    version = info.get("buildVersion")
    if version:
        result.conclude(Verdict.RESOLVED, f"API erreichbar, EUDAMED-Build {version}.")
    else:
        result.conclude(
            Verdict.PARTIAL,
            "API antwortet, liefert aber kein `buildVersion` — Feldname hat sich evtl. geändert.",
        )
    return result
