# The query modules of the public EUDAMED web UI

Measured on 2026-08-19 against production, prompted by the question whether the
eight publicly visible query screens (plus "Market Surveillance reports") are
actually backed by working filters — or whether they are displayed while the
API silently discards what you type.

Everything here is measured. The endpoints come from the configuration table in
the UI's `main.<hash>.js` bundle (which contains the complete screen → endpoint
mapping); the record counts from one call each with `size=1`.

## The nine screens and their endpoints

Base: `https://ec.europa.eu/tools/eudamed/api`

| Menu | Screen | Endpoint | Records | Notes |
|---|---|---|---|---|
| Actors | Economic Operators | `api/eos` | 48,830+ | only `name` works as a search filter (plus `srn`, `countryIso2Code`, `actorTypeCode`) |
| Actors | Competent Authorities | `api/ses/competentAuthorities?notifiedBodyAndCertificate=false` | **96** | role in clear text |
| Actors | Designating Authorities | same, `=true` | **28** | |
| Actors | Notified Bodies | `api/ses/` (paginated list), `api/ses/notifiedBodies` (short list) | **70** | NB number as identifier (`2460`), no SRN |
| UDI/Devices | Devices/SPPs | `api/devices/udiDiData` | — | filter behaviour: see [filter-matrix.md](filter-matrix.md) |
| UDI/Devices | EMDN | `api/devices/nomenclatures/` | 8,516 | full tree alternatively at `webgate.ec.europa.eu/dyna2/emdn/api/nomenclature?id=%23` |
| Certificates | Certificates | `api/certificates/search/` · refused ones: `api/applications/search/` | **4,472** | see measurement below |
| Certificates | NB-Monitoring Summaries | `api/summaryReports` | **0** | empty; Spring-Page format, responds cleanly |
| Market Surveillance | Market Surveillance reports | `api/msu/fourYearReviewReports/search/` · detail: `…/{uuid}` | **4** | four-year reports by authorities — **not vigilance data** |

Found outside the menu:

| Purpose | Endpoint | Finding |
|---|---|---|
| Merger/acquisition relations | `api/actorRelations/{ulid}/versions` | exists, HTTP 200; empty (`[]`) for all ten sampled entries of a large group manufacturer. The M&A feature announced in release notes 2.27.5 is wired up but carries nothing verifiable yet. |
| NB detail | `api/actors/{uuid}/publicInformationSearch` | separate detail path for notified bodies |

## Answer to the core question: are filters discarded?

**Both, and systematically — same as the device search.** Measured against
`api/certificates/search/`:

| Call | Hits |
|---|---|
| no filter | 4,472 |
| `actorSrn=DE-MF-000006413` | **1** — the filter works server-side |
| `actorSrn=XX-MF-999999999` (fabricated) | 0 — it really checks |
| `voelligUnbekannt=xyz` (fabricated parameter) | 4,472 — **silently discarded** |

The known finding from [filter-matrix.md](filter-matrix.md) holds for the whole
UI API: a wrong parameter name looks exactly like a successful call. Whoever
integrates one of these endpoints measures the control probe first.

Parameter names of the certificate search (captured from a real UI call):

    page, pageSize, size, iso2Code, languageIso2Code,
    sort=notifiedBodySrn,asc
    entityTypeCode=certificate.certificates | (counterpart for refused applications)
    actorSrn=<SRN>

Response fields per certificate: `ulid, uuid, notifiedBodySrn, actorSrn,
actorName, mfStatus, prStatus, certificateNumber, certificateType,
issueDate, expiryDate, startingValidityDate, certificateStatus,
versionNumber, latestVersion, versionState …`

## Interpretation notes

1. **The two certificate sources are disjoint and cover different regulatory
   generations.** The device-linked path (one request per device variant via
   the Basic-UDI detail, see the [official-api.md](official-api.md) companion
   and probe 04) yields almost exclusively MDD/AIMDD legacy certificates and
   reaches only a fraction of devices; the certificate-search module yields
   MDR/IVDR certificates at manufacturer level. In one measured corpus the
   device-linked path returned 550 certificates (544 of them MDD/AIMDD legacy,
   reaching 517 of 11,946 devices) while the search module returned 4,055
   certificates, all MDR/IVDR, in 14 requests. **Neither source alone answers
   "is this device certified?"** — a manufacturer-level certificate must never
   be presented as proof for an individual device.
2. **Market surveillance ≠ vigilance.** The four reports are four-year
   overviews by authorities (Art. 111 MDR). Recalls and field safety notices
   remain unreachable — the public UI's feature flag `ffVigFsn` is off (see
   [official-api.md](official-api.md) §1).
3. **CA/DA/NB lists are tiny** — all three together are fewer than 200
   entries.
4. **NB monitoring is empty** (0 records) — worth re-checking, not worth code.

*Measured 2026-08-19 against production. Re-verified 2026-08-24 against production 2.27.3 — no structural drift; counts grew, as expected. If you repeat any of this and get different values, that is not a contradiction — it is the reason `watch/apiwacht.py` exists.*
