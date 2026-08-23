# EUDAMED API gotchas

The traps, collected. Each one is measured; the longer story sits in
[official-api.md](official-api.md), [filter-matrix.md](filter-matrix.md) and
[module-matrix.md](module-matrix.md). "UI API" is the unofficial interface
behind the public web UI (`ec.europa.eu/tools/eudamed/api`); "official API" is
the documented Datalake API
(`api.datalake.sante.service.ec.europa.eu/eudamed`).

## 1. The UI API silently discards unknown parameters

HTTP 200, plausible hit count, no warning — just unfiltered. A typo in
`riskClassCode` looks exactly like a successful filtered query. This holds
across the whole UI API (device search *and* certificate search, measured).
**Always run a control probe**: send a fabricated parameter and confirm the
hit count does *not* change; only then does "unchanged" prove "ignored".
The official API is the opposite: unknown parameters are HTTP 400.

## 2. `NOMENCLATURE_CODE` values carry a leading space (official API)

The stored values look like `" Z12011480"`. `NOMENCLATURE_CODE=Q010601`
returns 0 records — which reads as "does not exist" — while
`NOMENCLATURE_CODE=%20Q010601` returns 1000+.

## 3. Prefix vs. exact: the same concept, two semantics

The UI API's `cndCode` searches by **prefix** (`Q01` includes all sub-nodes);
the official API's `NOMENCLATURE_CODE` matches **exactly** (and see the space,
above). A group query ported naively from one API to the other silently loses
all deeper leaves. Also: the official API stores **one** nomenclature code per
device while the underlying data is multi-valued.

## 4. `TRADE_NAME` matches exactly (official API)

`TRADE_NAME=ExacTrac` → 0 records, although devices whose trade name starts
with that string are registered. A partial name yields an empty result
indistinguishable from "not registered". The UI API's `tradeName` searches by
substring.

## 5. `RISK_CLASS_ID` wants numeric IDs, not code strings (official API)

`RISK_CLASS_ID=class-iii` is rejected — but the parameter itself works:
`RISK_CLASS_ID=-10.0` filters. Easy to misread as "parameter rejected". The
numeric IDs resolve via the `reference` endpoint (`{ID, CODE, LANGUAGE,
VALUE}`, multilingual).

## 6. Pagination differs on both sides

UI API: `page` is **0-based** (the openregulatory spec says it starts at 1;
measured, it starts at 0), maximum `pageSize` is 300. Official API: no
page/offset/limit at all — a cursor in `nextLink` (`$after` token), 1000
records per page.

## 7. An empty certificate list does not mean "uncertified"

In the device-linked path only a fraction of devices carry certificate data at
all — and coverage is so uneven that small samples swing wildly (measured:
3 of 12 in one sample, 0 of 12 in a later sample of the same group). Empty
means "not entered", not "no certificate".

## 8. The two certificate sources are disjoint

The device-linked path (Basic-UDI detail) yields almost exclusively MDD/AIMDD
legacy certificates; the certificate-search module yields MDR/IVDR
certificates at **manufacturer** level. Neither alone answers "is this device
certified?", and a manufacturer-level certificate is not proof for an
individual device.

## 9. Recalls and field safety notices are not publicly available

The UI's feature flag `ffVigFsn` is **off** (measured via
`configurationParameters?scope=PUBLIC`), and the vigilance services run over
DTX (authenticated machine-to-machine, onboarding required). No public API —
official or unofficial — carries recall data.

## 10. Rate limiting and User-Agent blocking

The UI API returns HTTP 429 under load, and blocks the default
`python-requests` User-Agent. HTTP 500 is a normal, retryable condition (a
server-side timeout), not a bug in your client. Be polite: identify yourself,
pause between requests (this repo uses 1–2 s), retry with backoff.

## 11. One SRN is one legal entity, not a corporate group

Large manufacturers register many legal entities. Aggregating "devices of
manufacturer X" by a single SRN undercounts the group. The actor module
announces M&A relations (release 2.27.5); the endpoint exists
(`api/actorRelations/{ulid}/versions`) but carried only empty lists when
measured.

## 12. Version numbers disagree with each other

Web page, release-notes PDF and the `applicationInfo` endpoint named three
different versions in the same week (2.27.0 / 2.27.5 / 2.27.3). Trust the
measured one: `api/applicationInfo` returns `buildVersion`.

## 13. Search-result records are sparse; details live one level deeper

Many fields of a UI search hit are `null` (`applicableLegislation`,
`deviceModel`, `sterile`, …) and only fill in the device/Basic-UDI detail
calls. A `null` in the search result does not mean the device lacks the
property.
