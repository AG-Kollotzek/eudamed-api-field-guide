# EUDAMED API Field Guide

Measured documentation of the two public APIs of
[EUDAMED](https://ec.europa.eu/tools/eudamed/eudamed), the European database
on medical devices — with the probe scripts to reproduce every finding.

**Everything here is measured. Every finding carries the command to repeat
it.** EUDAMED is under active development and changes without notice; a claim
you cannot re-measure is a claim you cannot trust. The
[repository](https://github.com/AG-Kollotzek/eudamed-api-field-guide)
therefore ships its evidence as runnable code, plus a watch script that
re-measures the findings on a schedule and records drift.

## Start here

| Document | What it answers |
|---|---|
| [Gotchas](gotchas.md) | The traps, collected. Read this before writing a line of integration code. |
| [The official API](official-api.md) | What the Datalake API can and cannot do — parameters, cursor pagination, the 60 fields per device, feature flags, SSCP. |
| [Filter matrix](filter-matrix.md) | Which query parameters of the UI device search actually work, and which are silently discarded. |
| [Module matrix](module-matrix.md) | All nine public UI screens and the endpoints behind them, including the certificate search. |
| [Certificate census](certificate-census.md) | How fast the certificate module is filling during the mandatory phase — one measurement per day. |
| [Changelog](changelog.md) | What has changed since, as recorded by the watch. |

## The one thing to know before you start

EUDAMED's two certificate sources are **disjoint**. The device-linked path
carries almost only MDD/AIMDD legacy certificates; the certificate-search
module carries MDR/IVDR certificates at *manufacturer* level. Neither alone
answers "is this device certified?", and a manufacturer-level certificate is
not proof for an individual device. The details are in
[gotcha 8](gotchas.md#8-the-two-certificate-sources-are-disjoint).

The second thing: the UI API **silently discards unknown parameters**. A typo
in a filter name returns HTTP 200 with a plausible, unfiltered result. Always
send a fabricated parameter as a control probe before trusting a filter.

## Reproduce any of it

```bash
git clone https://github.com/AG-Kollotzek/eudamed-api-field-guide
cd eudamed-api-field-guide
pip install requests

python -m probes.run_probes --no-cache --report PROBE_RESULTS.md
python watch/apiwacht.py --jetzt
python scripts/compare_sources.py --srn DE-MF-000006183
```

These are public endpoints of publicly funded infrastructure. The clients
here identify themselves, pause 1–2 s between requests and treat HTTP 429/500
as a signal to back off — please keep it that way.

## Who maintains this

The [IGRT Lab](https://igrt-lab.i-med.ac.at) at the
[Medical University of Innsbruck](https://www.i-med.ac.at/). Questions,
corrections and additions are welcome through the
[issue tracker](https://github.com/AG-Kollotzek/eudamed-api-field-guide/issues)
— re-run a probe and get a different result, and that is not a contradiction
but a change worth recording.

Code and documentation are MIT-licensed. The measurement series under
`data/census/` is derived from EUDAMED and carries the Commission's own terms
(CC BY 4.0 under Decision 2011/833/EU) —
[details and attribution](https://github.com/AG-Kollotzek/eudamed-api-field-guide/blob/main/data/census/README.md).
