# The official EUDAMED public API — findings from 2026-08-16

For years, the only programmatic access to EUDAMED was the **unofficial** API
behind the public web UI (`ec.europa.eu/tools/eudamed/api/…`). On 2026-08-16 a
review of the then-current release surfaced an **official, documented, publicly
accessible API**. This document records what it can do, what it cannot, and
what follows.

Everything here is measured. Every finding carries the command to repeat it.

---

## 1. How it was found

Not by searching — by watching the traffic.

The official web UI queries an endpoint at startup that appears in neither of
the two community reference repos:

```bash
curl -s "https://ec.europa.eu/tools/eudamed/api/configurationParameters?scope=PUBLIC&languageIso2Code=en"
```

It returns **twelve feature flags** with which the Commission controls what
appears on the public site at all. As of 2026-08-16:

| Flag | Value | Meaning per its own description |
|---|---|---|
| `ffDevice` | **on** | device display on the public site |
| `ffNomenclature` | **on** | EMDN nomenclature |
| `ffNews` | **on** | news |
| `ffCertificate` | **on** | certificate display |
| `ffNotifiedBodies` | **on** | notified bodies |
| `ffSscpi` | **on** | SSCP display (see section 5) |
| `ffRelatedDevice` | **on** | related devices |
| `ffAccessibilityStatement` | **on** | accessibility statement |
| `ffPublicDataApiFunction` | **on** | **link to the public data API** |
| `ffSponsor` | off | sponsor actors |
| `ffDeviceSubStatus` | off | device sub-status |
| `ffVigFsn` | **off** | **field safety notices from vigilance** |

Two flags answer questions that could previously only be asserted:

* **`ffVigFsn = off`.** Recalls and field safety notices are not publicly
  visible. "They don't publish that" is now measured rather than guessed.
* **`ffPublicDataApiFunction = on`**, with a URL in its value:
  `https://developer.datalake.sante.service.ec.europa.eu/api-details#api=94b9e658-d721-4b58-8d96-022c490f7a17`

`watch/apiwacht.py` reads this table on every run. If a flag flips, it lands
in the changelog.

---

## 2. What the official API is

**Base:** `https://api.datalake.sante.service.ec.europa.eu/eudamed/`

* **No key, no sign-up.** Measured: HTTP 200 without any header.
* **Versioned:** `api-version=v1.0` is mandatory.
* **Documented:** OpenAPI 3 (YAML and JSON), OpenAPI 2, WADL, changelog — in
  the DG SANTE developer portal.
* **Three groups:** `actors`, `reference`, `udi`.
* `format` is mandatory and accepts `json` and **`csv`**.

### The most important difference from the unofficial API

> The official API **rejects unknown parameters with HTTP 400.**

```bash
curl -s "https://api.datalake.sante.service.ec.europa.eu/eudamed/udi?format=json&api-version=v1.0&CND_CODE=Q01"
# {"error":{"code":"BadRequest","message":"Invalid Query Parameter: CND_CODE","status":400}}
```

This removes the most dangerous property of the unofficial API — the one the
whole [filter-matrix.md](filter-matrix.md) exists to guard against. A typo in
a parameter name is an error here, not a silently unfiltered list.

---

## 3. What `udi` can do — measured

### Accepted and rejected parameters

Each probed with a throwaway value; the HTTP status says whether the parameter
exists:

| Parameter | | Parameter | |
|---|---|---|---|
| `PRIMARY_DI` | ✅ 200 | `MF_NAME` | ❌ 400 |
| `BASIC_UDI` | ✅ 200 | `IMPLANTABLE` | ❌ 400 |
| `MF_SRN` | ✅ 200 | `STERILE` | ❌ 400 |
| `TRADE_NAME` | ✅ 200 | `UUID` | ❌ 400 |
| `DEVICE_NAME` | ✅ 200 | `DEVICE_STATUS_TYPE_ID` | ❌ 400 |
| `NOMENCLATURE_CODE` | ✅ 200 | | |
| `REFERENCE` | ✅ 200 | | |
| `RISK_CLASS_ID` | ✅ 200 (see correction below) | | |
| `APPLICABLE_LEGISLATION_ID` | ✅ 200 (see correction below) | | |

```bash
for p in PRIMARY_DI MF_SRN NOMENCLATURE_CODE RISK_CLASS_ID IMPLANTABLE; do
  printf "%-20s " "$p"
  curl -s -o /dev/null -w "%{http_code}\n" \
    "https://api.datalake.sante.service.ec.europa.eu/eudamed/udi?format=json&api-version=v1.0&$p=x"
done
```

### The trap: a leading space

`NOMENCLATURE_CODE` matches **exactly** — and the stored values carry a
leading space (`" Z12011480"`). Without it the query matches nothing:

```bash
# 0 records — looks like "does not exist"
…&NOMENCLATURE_CODE=Q010601
# 1000 records
…&NOMENCLATURE_CODE=%20Q010601
```

There is **no prefix search**: `%20Q0106` returns 0 hits although
`%20Q010601` sits below it. (The unofficial API's `cndCode` searches by
prefix — the same query concept behaves differently on the two APIs.)

### Pagination

No `page`, `offset`, `skip`, `limit`, `$top` — all eight probed variants
answer HTTP 400. Instead a **cursor**: the response carries, next to `value`,
a field `nextLink` with an opaque `$after` token.

```
Q010601, page 1: 1000 records + nextLink
Q010601, page 2:   81 records, no nextLink   -> 1081 devices total
```

### The 60 fields per device

```
ACTIVE                  ACTOR_ABBREVIATED_NAMES  ADMINISTERING_MEDICINE
ANIMAL_TISSUES          APPLICABLE_LEGISLATION_ID AR_ACTOR_NAMES
AR_NAME                 AR_SRN                   BASIC_UDI
BASIC_UDI_DATA_ULID     BASIC_UDI_DATA_UUID      CMR_SUBSTANCE
COMPANION_DIAGNOSTICS   CONTAINER_PACKAGE_DIS    DEVICE_CRITERION
DEVICE_MODEL            DEVICE_NAME              DEVICE_STATUS_TYPE_ID
DIRECT_MARKETING_DI     ENDOCRINE_DISRUPTOR      HUMAN_PRODUCT
HUMAN_TISSUES           ID                       IMPLANTABLE
INSTRUMENT              KIT                      LATEST_VERSION
LATEX                   MEASURING_FUNCTION       MEDICAL_PURPOSE
MEDICINAL_PRODUCT       MF_ACTOR_NAMES           MF_NAME
MF_SRN                  MICROBIAL_SUBSTANCES     MULTI_COMPONENT_ID
NEAR_PATIENT_TESTING    NEW_DEVICE               NOMENCLATURE_CODE
PLACED_ON_THE_MARKET_ID PRIMARY_DI               PROFESSIONAL_TESTING
REAGENT                 REFERENCE                REPROCESSED
REUSABLE                RISK_CLASS_ID            SECONDARY_DI
SELF_TESTING            SPECIAL_DEVICE_TYPE_ID   STATUS_ID
STERILE                 STERILIZATION            SUBSTATUSES
SUTURES                 TRADE_NAME               UDI_DI_DATA_ULID
UNIT_OF_USE_DI          UUID                     VERSION_NUMBER
```

**This is the actual find.** Note in particular: `IMPLANTABLE`, `STERILE`,
`REUSABLE`, `LATEX`, `MEASURING_FUNCTION`, `ANIMAL_TISSUES`, `HUMAN_TISSUES`,
`CMR_SUBSTANCE`, `SUTURES`, `KIT`, `REPROCESSED`, `SELF_TESTING`,
`NEAR_PATIENT_TESTING`, `COMPANION_DIAGNOSTICS`, `ENDOCRINE_DISRUPTOR`,
`MICROBIAL_SUBSTANCES`, `MEDICINAL_PRODUCT`, `ADMINISTERING_MEDICINE`.

Exactly these product characteristics are what the unofficial API **cannot
filter on** (see [filter-matrix.md](filter-matrix.md)) and only reveals via a
detail call *per device* (two requests each).

### The measurement that decides everything

```bash
curl -s -o /dev/null -w "%{time_total}s %{size_download} B\n" \
 "https://api.datalake.sante.service.ec.europa.eu/eudamed/udi?format=json&api-version=v1.0&MF_SRN=DE-MF-000006183"
# 21.9s 922023 B  -> 406 devices, 60 fields, ONE request
```

| | official API | per-device detail calls (unofficial) |
|---|---|---|
| 406 devices with all product characteristics | **1 request, 22 s** | 812 requests, ~22 min |

Factor 60.

---

## 4. What it CANNOT do

Three gaps, each of which prevents treating it as a full replacement for the
unofficial API:

1. **No certificates.** There is only `actors`, `reference`, `udi` —
   `/certificates` answers 404 (probe 07).
2. **No prefix/group search.** "A whole EMDN group including sub-nodes in one
   query" only works on the unofficial API. Expanding a group locally into
   leaf codes multiplies requests: measured against a local EMDN tree, `Q0106`
   has 2 leaf codes, `Z110104` has 5, `J01` has 55, `Z1101` has 59, `P09` has
   **208**.
3. **No filter on market status.** `DEVICE_STATUS_TYPE_ID` is rejected
   (HTTP 400); the value only exists as a numeric ID in the record and must be
   filtered locally.

### What `reference` provides in exchange

```bash
curl -s "https://api.datalake.sante.service.ec.europa.eu/eudamed/reference?format=json&api-version=v1.0" | head -c 300
```

136 kB of entries shaped `{ID, CODE, LANGUAGE, VALUE}` — the code lists,
**multilingual**. This resolves `RISK_CLASS_ID` and friends, and is the clean
source for displaying human-readable labels instead of raw codes.

---

## 5. SSCP — summary of safety and clinical performance

The flag `ffSscpi` is **on**. The Basic-UDI record of the unofficial API
carries the field `linkedSscp`:

```json
{"ulid": "01KZE3W6CJGG7J7TVD5WGR8BM5", "validated": true,
 "uuid": "3f36d942-…", "referenceNumber": "D2217384",
 "revisionNumber": "04", "issueDate": "2026-07-09",
 "versionNumber": 1, "inactive": false, "inactiveStatusDate": null}
```

**This is metadata only** — no text, no link to a PDF.

Coverage, measured on class-III devices:

* Categories J and P mixed: **6 of 12** carry a reference.
* First sample, category P only: **0 of 8**. Coverage is uneven; a small
  sample misleads.

The document itself was not reachable via guessed paths (`/sscps/{uuid}`,
`/sscp/{uuid}`, `/documents/{uuid}` — all 404 or not JSON). The reliable route
would be the same as for probe 04: capture the official UI's traffic on an
SSCP page. **That is the next open step.**

For context: an SSCP under Art. 32 MDR is the only clinical document in
EUDAMED — intended purpose, summary of the clinical evaluation, residual risks
and side effects, PMCF, intended user profile. Mandatory for class III and
implants.

---

## 6. From the release notes 2.27.5

* **Market surveillance services** (`MSU_PROCEDURE.GET`,
  `ANNUAL_SUMMARY_REPORT.GET`, `FINAL_INSPECTION_REPORT.GET`,
  `FOUR_YEAR_REPORT.GET`) run via **DTX** — machine-to-machine for
  authorities, notified bodies and economic operators with onboarding through
  an access point. Together with `ffVigFsn = off` this means: **recalls stay
  unreachable through any public API.**
* **DTX gets throttling**: messages are rejected on "unusual traffic" within
  24 hours. Nothing of the kind is documented for the public API — the
  direction is visible nonetheless and argues for client-side restraint
  (this repo's clients pause 1–2 s between requests).
* **Mergers & acquisitions in the actor module**: acquisition relations are
  now visible in actor details of search results — relevant for anyone
  aggregating devices per corporate group (a single SRN is one legal entity,
  not the group).
* **Certificates**: CECP (Clinical Evaluation Consultation Procedure) and
  Mechanism for Scrutiny are newly available — both concern class III.

### Three version numbers that do not match

| Source | Version |
|---|---|
| information centre (web page) | 2.27.0, published 2026-07-24 |
| release notes (PDF) | **2.27.5** |
| `applicationInfo` of production, measured 2026-08-15 | 2.27.3 |

When three sources name three versions, the measured one is the one to trust.
`watch/apiwacht.py` flags a change of `buildVersion` as notable.

---

## 7. Choosing between the two APIs

The two access paths serve the **same database** (measured — see
[changelog.md](changelog.md), source comparison of 2026-08-17) but differ in
**filter semantics** (prefix vs. exact) and **field scope** (60 product-
characteristic fields vs. certificates). Neither replaces the other; which one
answers a given question depends on the filters the question needs. Measure
before you route — and when you combine records from both, label each row
with its source: the two sides return different field sets for the same
device.

---

## 8. Open points for the next pass

1. **SSCP document**: find the endpoint via the official UI's traffic. Only
   then is it decidable whether more than metadata is publicly reachable.
2. **`actors` semantics.** `NAME=Brainlab` returned 0 records; the parameter
   semantics are unresolved.
3. **CSV format** (`format=csv`) — possibly the cheaper path for large
   extracts.

---

*Measured 2026-08-16 against production 2.27.3. Re-verified 2026-08-24 against production 2.27.3 — no structural drift; counts grew, as expected. If you repeat any of this and get different values, that is not a contradiction — it is the reason `watch/apiwacht.py` exists.*

---

> **Correction 2026-08-17 (found by `watch/apiwacht.py`, see
> [changelog.md](changelog.md))**
>
> Section 3 originally listed `RISK_CLASS_ID` as "rejected (HTTP 400)". That
> confused parameter and value: `RISK_CLASS_ID=class-iii` is rejected,
> `RISK_CLASS_ID=-10.0` filters server-side. The same holds for
> `APPLICABLE_LEGISLATION_ID`. Measured against one manufacturer
> (`MF_SRN=DE-MF-000006183`, 406 devices): `RISK_CLASS_ID=-10.0` (class III)
> → 4, `-203.0` (class I) → 235; `APPLICABLE_LEGISLATION_ID=-197.0` (MDR)
> → 330, `-53.0` (MDD) → 76 — and 330 + 76 = 406, an exact partition. The
> numeric IDs resolve via the `reference` endpoint.
>
> Also corrected: `TRADE_NAME` matches **exactly**, not as substring
> (`TRADE_NAME=ExacTrac` → 0 records although devices with that trade-name
> prefix exist). A partial name silently returns an empty result that looks
> like "not registered". The unofficial API's `tradeName` searches by
> substring.
>
> Also withdrawn: an earlier claim that the two APIs showed a 10-record
> discrepancy ("two sources, two truths"). The two counts were taken on
> **different days**. Compared within the same minute, the official result is
> a complete subset of the UI result (406 = 406; 1081 of 1082, the one extra
> being prefix breadth). It is one database with two access paths.
