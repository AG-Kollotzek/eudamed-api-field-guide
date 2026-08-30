# The certificate census

How fast is EUDAMED's certificate module filling up? Since **2026-05-28**
the first EUDAMED modules are mandatory, and notified bodies may backfill
legacy certificates until **2027-05-28**. This page tracks the fill level.

![Certificate fill curve](certificate-census.svg)

**Cadence:** a cron measures once a day (Mon–Sat) and once on Sunday with a
full-table dump. Documented anchors predate that cron and are marked as such
in the table. So far the series holds **1 measured point** and 2 documented anchors.

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

| Date | Certificates | MDR | IVDR | Refused | Source |
|---|---|---|---|---|---|
| 2026-08-19 | 4,472 | — | — | — | documented |
| 2026-08-23 | 4,538 | — | — | — | documented |
| 2026-08-30 | 4,654 | — | — | — | census |

Gap detection needs at least two daily measurements; there is 1 so far. It is therefore not yet meaningful to say the series has no gaps.

## Reproduce

```bash
# one census point (in the AskEUDAMED tool repo; 7 requests)
python scripts/zaehlstand.py

# re-render this page from the checked-in series
python scripts/fill_level.py
```

Raw series: [`data/census/`](../data/census/) — seed anchors with their
provenance, the daily JSONL, and dated dumps of the certificate table.

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

*Last data point: 2026-08-30. Deadline dates should be re-verified against
Regulation (EU) 2024/1860 and the implementing decision before citing.*
