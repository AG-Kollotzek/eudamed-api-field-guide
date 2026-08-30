# Census data — source, licence, contents

## Licence: this directory is not MIT

The repository's `LICENSE` (MIT) covers the **code and documentation** in
this repository. It does **not** cover the files in this directory: they are
derived from EUDAMED, a database of the European Commission, and no one
outside the Commission can place that data under MIT.

The applicable terms are the Commission's own. Commission documents are
reusable under **Decision 2011/833/EU**, and the
[legal notice](https://commission.europa.eu/legal-notice_en) places content
under **CC BY 4.0** unless stated otherwise. CC BY 4.0 §4 explicitly licenses
the *sui generis* database right as well, which matters here because the
weekly files are complete extracts of a substantial part of a database.

Attribution is the condition of that licence, so it travels with the data:

> Contains data from EUDAMED, © European Union, 1995–2026, reused under
> [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The European
> Commission is not responsible for any use made of this material, and this
> derived dataset is not endorsed by it.

Anyone reusing these files carries the same attribution forward.

## What is in here

| File | What it is |
|---|---|
| `seed.jsonl` | Documented anchor measurements taken before the daily collector existed. Each line carries the document and date it comes from, in `beleg`. |
| `bestand.jsonl` | The daily series. One line per day, appended, never rewritten. `status: "error"` lines record days on which EUDAMED could not be reached — outages are data, not gaps. |
| `zertifikate_YYYY-MM-DD*.jsonl.gz` | Dated extracts of the certificate-search table, one row per certificate record. Kept because the working table is overwritten by each weekly sync; only the **difference between two extracts** yields fill rates per notified body, status transitions and upload lag. |

## Two fields are deliberately missing

The extracts omit `actor_name` and `actor_srn`. Among manufacturers
registered as sole traders, `actor_name` carries the name of a natural
person, and republishing a full extract is a processing operation in its own
right. None of the analyses these extracts exist for needs the manufacturer's
identity — they are about notified bodies, statuses and dates. What remains
is certificate numbers, notified bodies, types, statuses and dates.

## Reading the numbers correctly

The counted quantity is `totalElements` of `api/certificates/search/` without
a filter: certificate **records in every status** (issued, supplemented,
amended, reissued, withdrawn, cancelled, suspended, restricted) in the
manufacturer-level search module. It is not a count of distinct certificates
— in the 2026-07-30 extract, 4,055 rows carried 3,958 distinct
`certificate_number` values — and it is not "all certificates in EUDAMED":
the device-linked legacy store is a separate, disjoint path. See
[../../docs/certificate-census.md](../../docs/certificate-census.md) for the
full method and
[../../docs/gotchas.md](../../docs/gotchas.md) for why the two certificate
sources cannot be added up.
