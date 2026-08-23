# Filterable parameters of the EUDAMED device search (UI API)

Measured on 2026-08-03 against the reference group `cndCode=Q0106` (71,630 hits)
on `https://ec.europa.eu/tools/eudamed/api/devices/udiDiData`.

**Why a control probe is mandatory:** this API silently discards unknown
parameters. A typo in a parameter name returns HTTP 200 with a plausible,
*unfiltered* result — indistinguishable from a successful filtered query.
Every measurement below is therefore anchored by a control probe: a fabricated
parameter (`voelligUnbekannt=xyz`) left the hit count unchanged, which proves
that "hit count unchanged" means "parameter ignored".

| Class | Meaning |
|---|---|
| WORKS | parameter changes the result set — usable server-side |
| IGNORED | parameter is silently discarded |
| REJECTED | server answers with an error |
| UNCLEAR | filters everything away; name probably right, value wrong |

## Product

| Parameter | Test value | Purpose | Result | Note |
|---|---|---|---|---|
| `applicableLegislation` | `refdata.applicable-legislation.mdr` | legislation, third spelling | **WORKS** | 60,809 of 71,630 (85%) |
| `deviceStatusCode` | `refdata.device-model-status.on-the-market` | market status | **WORKS** | 69,884 of 71,630 (98%) |
| `riskClassCode` | `refdata.risk-class.class-iia` | risk class | **WORKS** | 64,978 of 71,630 (91%) |
| `active` | `true` | active product | **IGNORED** | hit count unchanged |
| `animalTissues` | `true` | animal tissues | **IGNORED** | hit count unchanged |
| `annexXVIApplicable` | `true` | Annex XVI (no medical purpose) | **IGNORED** | hit count unchanged |
| `applicableLegislationCode` | `refdata.applicable-legislation.mdr` | legislation | **IGNORED** | hit count unchanged |
| `cmrSubstance` | `true` | CMR substances | **IGNORED** | hit count unchanged |
| `containsMedicinalSubstance` | `true` | contains medicinal substance | **IGNORED** | hit count unchanged |
| `humanTissues` | `true` | human tissues | **IGNORED** | hit count unchanged |
| `implantable` | `true` | implantable | **IGNORED** | hit count unchanged |
| `latex` | `true` | contains latex | **IGNORED** | hit count unchanged |
| `legislationCode` | `refdata.applicable-legislation.mdr` | legislation, other spelling | **IGNORED** | hit count unchanged |
| `measuringFunction` | `true` | measuring function | **IGNORED** | hit count unchanged |
| `multiComponent` | `true` | multi-component | **IGNORED** | hit count unchanged |
| `reusable` | `true` | reusable | **IGNORED** | hit count unchanged |
| `riskClass` | `refdata.risk-class.class-iia` | risk class, other spelling | **IGNORED** | hit count unchanged |
| `singleUse` | `true` | single use | **IGNORED** | hit count unchanged |
| `specialDeviceTypeCode` | `refdata.special-mdr-device-type.software` | software/orthopaedic | **IGNORED** | hit count unchanged |
| `sterile` | `true` | supplied sterile | **IGNORED** | hit count unchanged |

## Identifiers

| Parameter | Test value | Purpose | Result | Note |
|---|---|---|---|---|
| `basicUdi` | `++E494ZIRKON5F5` | Basic UDI-DI | **WORKS** | 489 of 71,630 (1%) |
| `deviceModel` | `Disc` | model name | **WORKS** | 2,037 of 71,630 (3%) |
| `reference` | `1` | reference/catalogue number | **WORKS** | 43,824 of 71,630 (61%) |
| `basicUdiCode` | `++E494ZIRKON5F5` | Basic UDI-DI, other spelling | **IGNORED** | hit count unchanged |
| `issuingAgencyCode` | `refdata.issuing-agency.hibcc` | UDI issuing agency | **IGNORED** | hit count unchanged |
| `secondaryDi` | `X` | secondary DI | **IGNORED** | hit count unchanged |

## Parties

| Parameter | Test value | Purpose | Result | Note |
|---|---|---|---|---|
| `srn` | `DE-MF-000006299` | manufacturer via SRN | **WORKS** | 1 of 71,630 (0%) |
| `authorisedRepresentativeSrn` | `DE-AR-000000001` | authorised representative | **IGNORED** | hit count unchanged |
| `importerSrn` | `DE-IM-000006314` | importer | **IGNORED** | hit count unchanged |
| `manufacturerName` | `Dentaurum` | manufacturer name | **IGNORED** | hit count unchanged |
| `manufacturerSrn` | `DE-MF-000006299` | manufacturer SRN, other spelling | **IGNORED** | hit count unchanged |
| `nbSrn` | `0197` | notified body, other spelling | **IGNORED** | hit count unchanged |
| `notifiedBodySrn` | `0197` | notified body | **IGNORED** | hit count unchanged |

## Market

| Parameter | Test value | Purpose | Result | Note |
|---|---|---|---|---|
| `countryIso2Code` | `DE` | country of availability | **IGNORED** | hit count unchanged |
| `marketCountryIso2Code` | `DE` | country, other spelling | **IGNORED** | hit count unchanged |
| `msWhereAvailable` | `DE` | country, third spelling | **IGNORED** | hit count unchanged |
| `placedOnTheMarket` | `DE` | placing on the market | **IGNORED** | hit count unchanged |

## Dates

| Parameter | Test value | Purpose | Result | Note |
|---|---|---|---|---|
| `certificateExpiryFrom` | `2026-01-01` | certificate expiry from | **IGNORED** | hit count unchanged |
| `expiryDateFrom` | `2026-01-01` | expiry from, other spelling | **IGNORED** | hit count unchanged |
| `issueDateFrom` | `2023-01-01` | issue date from | **IGNORED** | hit count unchanged |
| `lastUpdateDateFrom` | `2024-01-01` | last update from | **IGNORED** | hit count unchanged |
| `statusDateFrom` | `2024-01-01` | status date from | **IGNORED** | hit count unchanged |
| `versionDateFrom` | `2024-01-01` | record version from | **IGNORED** | hit count unchanged |

## Technical

| Parameter | Test value | Purpose | Result | Note |
|---|---|---|---|---|
| `latestVersion` | `true` | latest version only | **IGNORED** | hit count unchanged |
| `sort` | `primaryDi,ASC` | sorting | **IGNORED** | hit count unchanged |
| `versionStateCode` | `refdata.eudamed-entity-version-status.registered` | record state | **IGNORED** | hit count unchanged |

## Bottom line

Usable server-side: `riskClassCode`, `deviceStatusCode`, `applicableLegislation`,
`basicUdi`, `reference`, `deviceModel`, `srn`. Everything else must be filtered
locally — and anything you *believe* you are filtering server-side deserves a
control probe first.

*Measured 2026-08-03 against production. Re-verified 2026-08-24 against production 2.27.3 — no structural drift; counts grew, as expected. If you repeat any of this and get different values, that is not a contradiction — it is the reason `watch/apiwacht.py` exists.*
