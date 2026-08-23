# Changes to the EUDAMED interfaces

Maintained by `watch/apiwacht.py`, which appends a dated section per run
(newest at the bottom). What is recorded here is measured — what follows from
it is decided by a human.

> **Note on language:** the automated watch entries below are appended in
> German (the watch's report strings are German; the parameter names and
> numbers speak for themselves). The recurring phrases: *Wirksame Filter* =
> filters that work · *Geprüfte Codewerte* = probed code values ·
> *Präfixsuche über cndCode: ja* = prefix search via cndCode: yes ·
> *Seitenzählung: 0-basiert, höchstens 300 Treffer je Seite* = pagination
> 0-based, max 300 per page · *Unbekannter Parametername wird: verworfen* =
> unknown parameter name is: discarded · *Erstaufnahme* = baseline snapshot.

## 2026-08-15 — baseline

*Baseline snapshot. Data build 2.27.3, reference group Q010601: 1,071 hits.*

- Working filters: `riskClassCode`, `applicableLegislation`, `deviceStatusCode`
- Probed code values: 10
- Prefix search via `cndCode`: yes — {Q01: 411,806, Q0106: 71,630, Q010601: 1,071}
- Pagination: 0-based, max 300 hits per page
- Unknown parameter name: discarded silently · unknown code value: error
- Field names captured: device search (35), device detail (125), basic UDI (81),
  certificates (26), manufacturers (41)

## 2026-08-16 — feature flags added to the snapshot format

The watch now records the public site's feature-flag table
(`configurationParameters?scope=PUBLIC`). Baseline: ffDevice=on,
ffNomenclature=on, ffNews=on, ffCertificate=on, ffNotifiedBodies=on,
ffSscpi=on, ffRelatedDevice=on, ffAccessibilityStatement=on,
ffPublicDataApiFunction=on, ffSponsor=OFF, ffDeviceSubStatus=OFF,
**ffVigFsn=OFF**.

## 2026-08-17 — source comparison: do the two interfaces agree?

Trigger: an earlier claim that UI and official API showed a 10-record
discrepancy ("two sources, two truths"). The evidence did not hold — the two
counts came from **different days**. Measured with
`scripts/compare_sources.py`, both queries within the same time window:

**Clean comparison** (`--srn DE-MF-000006183`, both sides match exactly):

| | records |
|---|---|
| UI API | 406 |
| official API | 406 |
| in both | 406 |
| unexplained | **0** |

Agreement down to the individual device UUID.

**Semantic comparison** (`--cnd Q010601`; the UI searches by prefix, the
official API exactly):

| | records |
|---|---|
| UI API | 1,082 |
| official API | 1,081 |
| in both | 1,081 |
| UI only | 1 (prefix breadth — one deeper leaf) |
| official only | **0** |

The directed expectation *official ⊆ UI* holds completely. Not a single
record known only to the official side — which the exact-match search could
not produce anyway.

**Consequence:** it is **one** database with two access paths, not two data
sets. What differs is **filter semantics** (prefix vs. exact) and **field
scope** (60 product-characteristic fields vs. certificates) — not the
content. When combining records from both paths, still label each row with
its source, because the two sides return different fields.

## 2026-08-17 — the watch refutes three "measured" claims

On the first run of the watch extended to both interfaces, three statements
that had counted as measured since 2026-08-16 fell over:

**1. `RISK_CLASS_ID` and `APPLICABLE_LEGISLATION_ID` do filter.** The earlier
"rejected (HTTP 400)" conflated parameter and value: `class-iii` is rejected,
the numeric ID `-10.0` filters. Measured against `MF_SRN=DE-MF-000006183`:

| Filter | records |
|---|---|
| `MF_SRN` only | 406 |
| `+ RISK_CLASS_ID=-10.0` (class III) | 4 |
| `+ RISK_CLASS_ID=-203.0` (class I) | 235 |
| `+ APPLICABLE_LEGISLATION_ID=-197.0` (MDR) | 330 |
| `+ APPLICABLE_LEGISLATION_ID=-53.0` (MDD) | 76 |

330 + 76 = 406 — an exact partition, the same form of proof as for
`applicableLegislation` on the UI side.

**2. `TRADE_NAME` matches exactly, not as substring.**

    TRADE_NAME=ExacTrac                              ->    0 records
    TRADE_NAME=Drill Guide Depth Control ( 0-60 mm)  ->    1 record

A partial trade name silently returns an empty result that looks like "not
registered".

**3. `DEVICE_STATUS_TYPE_ID` is rejected** (HTTP 400). Market status remains
a local post-filter on the official API.

All three points are wired into `watch/apiwacht.py` as standing probes: if
any of them changes again, the next run reports it.

## 2026-08-23 — Erstaufnahme

*Erstaufnahme. Datenstand 2.27.3, Referenzgruppe Q010601: 1130 Treffer.*

Ausgangszustand festgehalten. Verglichen wird ab der nächsten Aufnahme.

- Wirksame Filter: `riskClassCode`, `applicableLegislation`, `deviceStatusCode`
- Geprüfte Codewerte: 10
- Präfixsuche über `cndCode`: ja {'Q01': 418861, 'Q0106': 74596, 'Q010601': 1130}
- Seitenzählung: 0-basiert, höchstens 300 Treffer je Seite
- Schalter der öffentlichen Seite: ffAccessibilityStatement=an, ffCertificate=an, ffDevice=an, ffDeviceSubStatus=AUS, ffNews=an, ffNomenclature=an, ffNotifiedBodies=an, ffPublicDataApiFunction=an, ffRelatedDevice=an, ffSponsor=AUS, ffSscpi=an, ffVigFsn=AUS
- Unbekannter Parametername wird: verworfen · unbekannter Codewert: fehler
- Aus der Wunschliste wirksam: keiner von 8
- Erfasste Feldnamen: geraetesuche (35), geraetedetail (135), basic_udi (81), zertifikate (26), hersteller (41)

## 2026-08-24 — pre-publication re-run

All seven probes, a watch snapshot and both source comparisons were re-run
before publishing this repo. No structural drift: prefix search, 0-based
pagination, filter behaviour, feature flags and the parameter findings all
hold; counts grew (Q010601: 1,071 → 1,130), as expected. One sampling note:
probe 04's 12-device sample this time contained **0** devices with
certificate data (3 of 12 in the original run) — certificate coverage in the
device-linked path is that uneven; see gotcha 7. Source comparison:
424 = 424 records for the reference SRN, unexplained deviation 0.0%.
