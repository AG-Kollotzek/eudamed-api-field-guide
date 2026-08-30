# EUDAMED API Field Guide

Measured documentation of the two public APIs of
[EUDAMED](https://ec.europa.eu/tools/eudamed/eudamed), the European database
on medical devices — with the probe scripts to reproduce every finding.

**Everything here is measured. Every finding carries the command to repeat
it.** EUDAMED is under active development and changes without notice; a claim
you cannot re-measure is a claim you cannot trust. This repo therefore ships
its evidence as runnable code, and a watch script that re-measures the
findings on a schedule and records drift in a changelog.

## The two APIs

| | UI API (unofficial) | Official public API |
|---|---|---|
| Base | `ec.europa.eu/tools/eudamed/api` | `api.datalake.sante.service.ec.europa.eu/eudamed` |
| Status | reverse-engineered, no contract | documented (OpenAPI), versioned |
| Unknown parameters | **silently discarded** | rejected with HTTP 400 |
| EMDN group search | prefix (`cndCode=Q01` includes sub-nodes) | exact match only (and values carry a leading space) |
| Certificates | yes (two disjoint sources — see below) | **no** |
| Product characteristics (implantable, sterile, …) | only via per-device detail calls | 60 fields in one request |
| Pagination | `page` (0-based), max 300/page | cursor (`nextLink`), 1000/page |

They serve the **same database** (measured: the official result set is a
complete subset of the UI result set when queried in the same time window) —
what differs is filter semantics and field scope.

## The documents

| Document | What it answers |
|---|---|
| [docs/official-api.md](docs/official-api.md) | What the official Datalake API can and cannot do — parameters, pagination, the 60 fields, feature flags, SSCP |
| [docs/filter-matrix.md](docs/filter-matrix.md) | Which query parameters of the UI device search actually work, and which are silently discarded |
| [docs/module-matrix.md](docs/module-matrix.md) | All nine public UI screens and their endpoints, incl. the certificate search |
| [docs/gotchas.md](docs/gotchas.md) | The traps, collected — start here |
| [docs/certificate-census.md](docs/certificate-census.md) | How fast the certificate module fills during the mandatory phase — one measurement per day |
| [docs/changelog.md](docs/changelog.md) | What has changed since, as recorded by the watch |
| [PROBE_RESULTS.md](PROBE_RESULTS.md) | Raw output of the probe runs |

**The single most important interpretation warning:** EUDAMED's two
certificate sources are disjoint. The device-linked path carries almost only
MDD/AIMDD legacy certificates; the certificate-search module carries MDR/IVDR
certificates at *manufacturer* level. Neither alone answers "is this device
certified?" — and a manufacturer-level certificate is not proof for an
individual device. Details in
[docs/module-matrix.md](docs/module-matrix.md).

## Reproduce the findings

```bash
git clone https://github.com/AG-Kollotzek/eudamed-api-field-guide
cd eudamed-api-field-guide
pip install requests

# the seven probes (one question each, ~a few dozen requests total)
python -m probes.run_probes --no-cache --report PROBE_RESULTS.md

# re-measure the watch's snapshot now and record drift in docs/changelog.md
python watch/apiwacht.py --jetzt

# verify that both APIs return the same records for the same query
python scripts/compare_sources.py --srn DE-MF-000006183
```

**Be polite.** These are public endpoints of publicly funded infrastructure.
The clients in this repo identify themselves, pause 1–2 s between requests
and treat HTTP 429/500 as a signal to back off — keep it that way. A full
probe run is a few dozen requests; don't loop it.

### Code layout

- `client/` — Python clients for both APIs (retry/backoff, raw-response
  cache, User-Agent handling). Plain `requests`, no other dependencies.
- `probes/` — one script per question, run via `python -m probes.run_probes`.
- `watch/apiwacht.py` — re-measures filter behaviour, code values, field
  names, feature flags and pagination every 14 days (or on demand) and
  appends findings to [docs/changelog.md](docs/changelog.md).
- `scripts/compare_sources.py` — same query against both APIs, set
  comparison of the returned device UUIDs.

Code identifiers and the watch's report strings are German (the repo grew out
of a German-language project); all documentation is English, and the
measured values speak for themselves. Translations welcome.

## Relation to prior work

Two community repos documented EUDAMED's APIs first, and this repo builds on
both:

- [openregulatory/eudamed-api](https://github.com/openregulatory/eudamed-api)
  — an OpenAPI 3.1 spec of four UI-API endpoints with clean response schemas.
  No query parameters beyond pagination, no certificates; one documented path
  no longer exists (measured 404, see probe 04 — the working path is
  `/devices/basicUdiData/udiDiData/{deviceUuid}`).
- [Delapro/EUDAMED](https://github.com/Delapro/EUDAMED) — a PowerShell client
  and an extensive README with real response dumps, covering certificates,
  EMDN and the official API's existence.

What this repo adds: systematic parameter probing with control probes (the
"silently discarded" trap), the official API measured in depth, the screen →
endpoint map, the feature-flag table, and a drift watch. Findings that
correct the reference repos are marked as such in the probes' output.

## Who maintains this

The [**IGRT Lab**](https://igrt-lab.i-med.ac.at) at the [Medical University of
Innsbruck](https://www.i-med.ac.at/). The measurements grew out of a research
project on making EUDAMED data usable for clinical medical-device questions.

Questions, corrections and additions are welcome through the issue tracker —
that is the fastest way to reach a human here, and it keeps the answer where
the next person will find it.

## Contributing

Re-run a probe and get a different result? That is not a contradiction —
EUDAMED changed. Open an issue with the probe output, or a PR against
[docs/changelog.md](docs/changelog.md). New probes welcome: one script, one
question, one verdict.


## License

MIT.
