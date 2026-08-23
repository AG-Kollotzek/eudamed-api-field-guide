# Probe-Ergebnisse (Phase 1)

Lauf vom 2026-08-23 22:50 UTC, Dauer 183s, 57 HTTP-Requests.

Beantwortet die offenen Fragen aus der Sichtung der Referenz-Repos (siehe README).
Rohantworten liegen in `raw_cache/`.

## Überblick

| # | Frage | Ergebnis |
|---|---|---|
| 01 | Erreichbarkeit und Versionsstand | ✅ GEKLÄRT |
| 02 | Paginierung: 0- oder 1-basiert, Größenlimit | ✅ GEKLÄRT |
| 03 | cndCode: Präfix-Suche oder exakter Match? | ✅ GEKLÄRT |
| 04 | Weg vom Suchtreffer zum Zertifikat | 🟡 TEILWEISE |
| 05 | Undokumentierte Filterparameter | ✅ GEKLÄRT |
| 06 | Zertifikatssuche: Feldnamen, Enums, PDF-Pfad | ✅ GEKLÄRT |
| 07 | Offizielle Datalake-API | ✅ GEKLÄRT |

---

## ✅ Probe 01 — Erreichbarkeit und Versionsstand

**Frage:** Antwortet die API, und welcher EUDAMED-Build läuft gerade?

**Ergebnis:** GEKLÄRT (1 Requests)

- Antwortzeit: 0.26s
- `buildVersion`: `2.27.3`
- `lastBuildDate`: `2026-06-09 15:00`
- `activeProfile`: `[prod]`
- **Fazit:** API erreichbar, EUDAMED-Build 2.27.3.

<details><summary>Rohdaten</summary>

```json
{
  "applicationInfo": {
    "buildVersion": "2.27.3",
    "lastBuildDate": "2026-06-09 15:00",
    "activeProfile": "[prod]"
  }
}
```

</details>

## ✅ Probe 02 — Paginierung: 0- oder 1-basiert, Größenlimit

**Frage:** Wie verhalten sich page/pageSize/size wirklich?

**Ergebnis:** GEKLÄRT (6 Requests)

- Referenzabfrage `cndCode=Q010601`: 1086 Treffer gesamt.
- `page=0` -> Antwortfeld `number` = `0`, 5 Treffer.
- `page=1` -> Antwortfeld `number` = `1`, 5 Treffer.
- Seite 0 und Seite 1 überschneiden sich nicht, und Seite 0 meldet `number=0`.
- `size=300` angefragt -> Antwort meldet `size=300`, `numberOfElements=300`.
- `size=500` angefragt -> Antwort meldet `size=300`, `numberOfElements=300`.
- Ungedeckelt `size=500` -> Antwort meldet `size=300`, `numberOfElements=300`.
- `size=1` -> `totalElements=1086` (identisch mit der Vollabfrage).
- **Fazit:** `page` ist 0-basiert — die openregulatory-Spec dokumentiert 'starts at 1', gemessen beginnt sie bei 0. Größenverhalten siehe oben.

<details><summary>Rohdaten</summary>

```json
{
  "page0_uuids": [
    "0fe8467e-2a43-4b46-a6ad-2f547281a754",
    "bc44cd7a-2da2-4cae-a1be-3efb2d194d46",
    "4ed0cea7-5132-42b4-8c1d-0a7b23f74aee",
    "f74f5faa-11af-4dfc-8851-a6e99fd6b437",
    "46edbfb3-326c-49ea-bb24-5772c4b4a250"
  ],
  "page1_uuids": [
    "03ff884f-46a5-4075-bac3-cd001ef8f1b6",
    "9c0847ca-d860-402b-9064-e2e35641b69d",
    "2e09500b-c9d4-4c0f-94de-6d64e2a75c44",
    "cb924bae-9786-4958-bdb0-aad0b1f21c65",
    "9a7d8b5b-175f-4f43-8329-db33166180fd"
  ],
  "zero_based": true,
  "size_300": {
    "size": 300,
    "numberOfElements": 300
  },
  "size_500": {
    "size": 300,
    "numberOfElements": 300
  },
  "size_500_uncapped": {
    "size": 300,
    "numberOfElements": 300
  },
  "count_trick_consistent": true
}
```

</details>

## ✅ Probe 03 — cndCode: Präfix-Suche oder exakter Match?

**Frage:** Liefert cndCode=Q01 auch die Unterknoten? Sind mehrere cndCode gleichzeitig möglich?

**Ergebnis:** GEKLÄRT (7 Requests)

- `cndCode=Q` -> 476891 Treffer
- `cndCode=Q01` -> 413863 Treffer
- `cndCode=Q0106` -> 72850 Treffer
- `cndCode=Q010601` -> 1086 Treffer
- `cndCode=Q010601` = 1086, `cndCode=Q010699` = 71765, beide zusammen = 0
- -> Unerwartetes Verhalten, evtl. UND-Verknüpfung.
- **Fazit:** `cndCode` sucht per Präfix. Gruppenabfragen gehen direkt über den Elternknoten — kein lokales Expandieren des EMDN-Baums nötig.

<details><summary>Rohdaten</summary>

```json
{
  "counts": {
    "Q": 476891,
    "Q01": 413863,
    "Q0106": 72850,
    "Q010601": 1086
  },
  "multi_cnd": {
    "single": [
      1086,
      71765
    ],
    "combined": 0
  }
}
```

</details>

## 🟡 Probe 04 — Weg vom Suchtreffer zum Zertifikat

**Frage:** Wie erreicht man deviceCertificateInfoList von einem Suchtreffer aus?

**Ergebnis:** TEILWEISE (15 Requests)

- Suchtreffer-Felder (befüllt): `basicUdi`, `basicUdiDataVersionNumber`, `basicUdiDiDataUlid`, `containerPackageCount`, `deviceStatusType`, `latestVersion`, `manufacturerName`, `manufacturerSrn`, `manufacturerStatus`, `primaryDi`, `reference`, `riskClass`, `tradeName`, `ulid`, `uuid`, `versionNumber`
- Suchtreffer-Felder (leer): `applicableLegislation`, `authorisedRepresentativeName`, `authorisedRepresentativeSrn`, `basicUdiDataUlid`, `basicUdiDataUuid`, `basicUdiDataVersionState`, `deviceCriterion`, `deviceModel`, `deviceName`, `issuingAgency`, `lastUpdateDate`, `manufacturerNames`, `mfOrPrSrn`, `multiComponent`, `sterile`, `versionState`
- `uuid` = `0fe8467e-2a43-4b46-a6ad-2f547281a754` (UUID), `basicUdiDiDataUlid` = `01M08KAK119ASTE3QTDT0TTQ8P` (ULID), `basicUdiDataUuid` = `None`
- ✅ `/devices/basicUdiData/udiDiData/{deviceUuid}` -> HTTP 200, 52 Felder.
- `deviceModel`: `CoCrW milling disc`
- `riskClass.code`: `refdata.risk-class.class-iia`
- `legislation.code`: `refdata.applicable-legislation.mdr`
- Gegenprobe `/devices/basicUdiData/{basicUdiDiDataUlid}` (Pfad laut openregulatory-Spec) -> HTTP 404
- **Datenabdeckung:** 0 von 12 Geräten haben Zertifikatsdaten (0 Zertifikate insgesamt).
- **Fazit:** Endpunkt erreichbar, aber in dieser Stichprobe hatte kein Gerät Zertifikatsdaten. Mit anderer Produktgruppe gegenprüfen.

<details><summary>Rohdaten</summary>

```json
{
  "search_entry": {
    "basicUdi": "426020785CoCrW-mill9X",
    "primaryDi": "04260580283705",
    "uuid": "0fe8467e-2a43-4b46-a6ad-2f547281a754",
    "ulid": "01M0DT4QA078HJ0CBV402YSB2N",
    "basicUdiDiDataUlid": "01M08KAK119ASTE3QTDT0TTQ8P",
    "riskClass": {
      "code": "refdata.risk-class.class-iia"
    },
    "tradeName": "Novobond Easy Disc (8 mm; mit Absatz)",
    "manufacturerName": "Scheftner GmbH",
    "manufacturerSrn": "DE-MF-000049419",
    "deviceStatusType": {
      "code": "refdata.device-model-status.on-the-market"
    },
    "manufacturerNames": null,
    "manufacturerStatus": {
      "code": "refdata.actor-status.active"
    },
    "latestVersion": true,
    "versionNumber": 1,
    "basicUdiDataUuid": null,
    "basicUdiDataUlid": null,
    "basicUdiDataVersionState": null,
    "versionState": null,
    "deviceName": null,
    "deviceModel": null,
    "lastUpdateDate": null,
    "reference": "MD140508",
    "basicUdiDataVersionNumber": 0,
    "issuingAgency": null,
    "containerPackageCount": 0,
    "mfOrPrSrn": null,
    "applicableLegislation": null,
    "authorisedRepresentativeSrn": null,
    "authorisedRepresentativeName": null,
    "sterile": null,
    "multiComponent": null,
    "deviceCriterion": null
  },
  "basic_udi_keys": [
    "active",
    "administeringMedicine",
    "animalTissues",
    "authorisedRepresentative",
    "basicUdi",
    "basicUdiType",
    "clinicalInvestigationApplicable",
    "clinicalInvestigationLinks",
    "companionDiagnostics",
    "containerType",
    "device",
    "deviceCertificateInfoList",
    "deviceCertificateInfoListForDisplay",
    "deviceCriterion",
    "deviceModel",
    "deviceModelApplicable",
    "deviceName",
    "discardedDate",
    "humanProduct",
    "humanTissues",
    "implantable",
    "instrument",
    "kit",
    "lastUpdated",
    "latestVersion",
    "legacyDeviceUdiDiApplicable",
    "legislation",
    "linkedSscp",
    "manufacturer",
    "measuringFunction",
    "medicalPurpose",
    "medicinalProduct",
    "microbialSubstances",
    "multiComponent",
    "nbDecision",
    "nearPatientTesting",
    "new",
    "professionalTesting",
    "reagent",
    "reusable",
    "riskClass",
    "selfTesting",
    "specialDeviceType",
    "specialDeviceTypeApplicable",
    "sutures",
    "typeExaminationApplicable",
    "udiDiData",
    "ulid",
    "uuid",
    "versionDate",
    "versionNumber",
    "versionState"
  ],
  "spec_path_error": "HTTP 404",
  "coverage": [
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    },
    {
      "manufacturer": "Scheftner GmbH",
      "riskClass": "refdata.risk-class.class-iia",
      "certs": 0
    }
  ]
}
```

</details>

## ✅ Probe 05 — Undokumentierte Filterparameter

**Frage:** Wirken riskClassCode, Legislation-, Datums- und NB-Filter auf /devices/udiDiData?

**Ergebnis:** GEKLÄRT (12 Requests)

- Basis: `cndCode=Q010601` -> **1086** Treffer.
- Kontrollprobe mit Fantasieparameter -> 1086 Treffer (unverändert -> unbekannte Parameter werden ignoriert)
- `riskClassCode=refdata.risk-class.class-iia` (Risikoklasse IIa) -> ✅ **1083** Treffer (von 1086) — wirkt.
- `riskClass=refdata.risk-class.class-iia` (Risikoklasse, alternativer Name) -> 1086 Treffer — unverändert, wirkt nicht.
- `applicableLegislationCode=refdata.applicable-legislation.mdr` (nur MDR) -> 1086 Treffer — unverändert, wirkt nicht.
- `legislationCode=refdata.applicable-legislation.mdr` (Legislation, alternativer Name) -> 1086 Treffer — unverändert, wirkt nicht.
- `notifiedBodySrn=0197` (nur Geräte mit TÜV-Rheinland-Zertifikat) -> 1086 Treffer — unverändert, wirkt nicht.
- `countryIso2Code=DE` (nur in DE verfügbar) -> 1086 Treffer — unverändert, wirkt nicht.
- `issueDateFrom=2023-01-01` (Ausstellungsdatum ab) -> 1086 Treffer — unverändert, wirkt nicht.
- `expiryDateFrom=2026-01-01` (Ablaufdatum ab) -> 1086 Treffer — unverändert, wirkt nicht.
- `certificateExpiryFrom=2026-01-01` (Ablaufdatum ab, alternativer Name) -> 1086 Treffer — unverändert, wirkt nicht.
- `versionDateFrom=2023-01-01` (Datensatz-Version ab) -> 1086 Treffer — unverändert, wirkt nicht.
- **Fazit:** Serverseitig wirksam: `riskClassCode`. Diese in Ein Client sollte ihn serverseitig nutzen statt lokal nachzufiltern. Alle übrigen bleiben clientseitige Filter.

<details><summary>Rohdaten</summary>

```json
{
  "ignores_unknown_params": true,
  "findings": {
    "riskClassCode": {
      "status": "wirkt",
      "count": 1083
    },
    "riskClass": {
      "status": "ignoriert",
      "count": 1086
    },
    "applicableLegislationCode": {
      "status": "ignoriert",
      "count": 1086
    },
    "legislationCode": {
      "status": "ignoriert",
      "count": 1086
    },
    "notifiedBodySrn": {
      "status": "ignoriert",
      "count": 1086
    },
    "countryIso2Code": {
      "status": "ignoriert",
      "count": 1086
    },
    "issueDateFrom": {
      "status": "ignoriert",
      "count": 1086
    },
    "expiryDateFrom": {
      "status": "ignoriert",
      "count": 1086
    },
    "certificateExpiryFrom": {
      "status": "ignoriert",
      "count": 1086
    },
    "versionDateFrom": {
      "status": "ignoriert",
      "count": 1086
    }
  },
  "working": [
    "riskClassCode"
  ]
}
```

</details>

## ✅ Probe 06 — Zertifikatssuche: Feldnamen, Enums, PDF-Pfad

**Frage:** Wie sehen die Daten der Zertifikatssuche wirklich aus?

**Ergebnis:** GEKLÄRT (4 Requests)

- Gesamtzahl Zertifikate: **4538**, geladen: 100.
- Felder (befüllt): `actorName`, `actorNames`, `actorSrn`, `certificateNumber`, `certificateStatus`, `certificateType`, `expiryDate`, `issueDate`, `latestVersion`, `mfStatus`, `notifiedBodySrn`, `startingValidityDate`, `ulid`, `uuid`, `versionNumber`, `versionState`
- Felder (leer): `arStatuses`, `authorizedRepresentativeSrns`, `prStatus`, `revisionNumber`
- Datumsfelder: `expiryDate`, `issueDate`, `startingValidityDate`
- -> Bestätigt: hier `expiryDate`, im Basic-UDI-Detail `certificateExpiry`. Im SQLite-Mapping auf `expiry_date` vereinheitlichen.
- `certificateType` — beobachtete Werte: `refdata.certificate-ivdr-type.technical-documentation` (60x), `refdata.certificate-ivdr-type.quality-management-system` (24x), `refdata.certificate-mdr-type.quality-management-system` (16x)
- `certificateStatus` — beobachtete Werte: `refdata.certificate-status.issued` (73x), `refdata.certificate-status.supplemented` (21x), `refdata.certificate-status.amended` (2x), `refdata.certificate-status.restricted` (1x), `refdata.certificate-status.withdrawn` (1x), `refdata.certificate-status.cancelled` (1x), `refdata.certificate-status.reissued` (1x)
- `notifiedBodySrn=0197` -> 705 von 4538 Zertifikaten (filtert).
- Detail-Felder: `acceptedDisclaimer`, `additionalStatusChangeReason`, `animalTissues`, `applicableLegislation`, `applicationReference`, `authorisedRepresentatives`, `cecpApplicable`, `cecpPublicDocuments`, `cecps`, `certificateId`, `certificateNumber`, `conditions`, `conditionsApplicable`, `decisionComments`, `decisionDate`, `decisionDocuments`, `discardAllowed`, `discardedDate`, `documents`, `eligibleBasicUdisForSscp`, `eligibleCecps`, `expiryDate`, `humanTissues`, `inVitroDiagnostics`, `initialRegistration`, `intendedMedicalPurpose`, `issueDate`, `ivdrMechanismOfScrutiny`, `languages`, `latestVersion`, `manufacturer`, `mechanismOfScrutinyEnabled`, `mosAddedBudis`, `mosOutsideEudamed`, `new`, `notifiedBody`, `otherDecisionReasons`, `precedingCertificates`, `precedingCertificatesUnregistered`, `producer`, `qmsMosType`, `revisionNumber`, `scopes`, `sppApplicable`, `sppType`, `sscpEnabled`, `sscps`, `startingDecisionApplicabilityDate`, `startingValidityDate`, `status`, `statusChangeReasons`, `sterile`, `type`, `ulid`, `uuid`, `versionDate`, `versionNumber`, `versionState`
- `documents[]`: 1 Eintrag/Einträge.
- Dokument-Felder: `fileContentType`, `fileSize`, `indexed`, `languages`, `new`, `originalFileName`, `primaryModuleName`, `referenceDocId`, `tempFileName`, `type`, `uuid`, `virusCheck`
- PDF-Download `None` -> 1691450 Bytes, gültiges PDF.
- **Fazit:** Feldnamen und Enum-Werte der Zertifikatssuche sind jetzt belegt (siehe oben).

<details><summary>Rohdaten</summary>

```json
{
  "certificate_search_entry": {
    "ulid": "01KTRNZDXJ464K52YBH502R56Q",
    "uuid": "c266239c-462c-4840-9017-4efac3289fbc",
    "notifiedBodySrn": "0633",
    "actorSrn": "DE-MF-000006413",
    "actorName": "AIRAmed GmbH",
    "actorNames": {
      "texts": [
        {
          "language": {
            "isoCode": "de",
            "name": "German"
          },
          "text": "AIRAmed GmbH",
          "allLanguagesApplicable": null
        }
      ],
      "textByDefaultLanguage": null
    },
    "mfStatus": {
      "DE-MF-000006413": {
        "code": "refdata.actor-status.active"
      }
    },
    "prStatus": null,
    "arStatuses": null,
    "certificateNumber": "Z-25-052-S-IX-E",
    "certificateType": {
      "code": "refdata.certificate-mdr-type.quality-management-system"
    },
    "issueDate": "2025-09-17T00:00:00",
    "expiryDate": "2030-09-16T00:00:00",
    "startingValidityDate": "2025-09-17T00:00:00",
    "certificateStatus": {
      "code": "refdata.certificate-status.issued"
    },
    "versionNumber": 1,
    "authorizedRepresentativeSrns": null,
    "latestVersion": true,
    "revisionNumber": null,
    "versionState": {
      "code": "refdata.eudamed-entity-version-status.registered"
    }
  },
  "date_fields": [
    "expiryDate",
    "issueDate",
    "startingValidityDate"
  ],
  "codes_certificateType": {
    "refdata.certificate-mdr-type.quality-management-system": 16,
    "refdata.certificate-ivdr-type.technical-documentation": 60,
    "refdata.certificate-ivdr-type.quality-management-system": 24
  },
  "codes_certificateStatus": {
    "refdata.certificate-status.issued": 73,
    "refdata.certificate-status.amended": 2,
    "refdata.certificate-status.supplemented": 21,
    "refdata.certificate-status.restricted": 1,
    "refdata.certificate-status.withdrawn": 1,
    "refdata.certificate-status.cancelled": 1,
    "refdata.certificate-status.reissued": 1
  },
  "nb_filter_works": true,
  "certificate_detail_keys": [
    "acceptedDisclaimer",
    "additionalStatusChangeReason",
    "animalTissues",
    "applicableLegislation",
    "applicationReference",
    "authorisedRepresentatives",
    "cecpApplicable",
    "cecpPublicDocuments",
    "cecps",
    "certificateId",
    "certificateNumber",
    "conditions",
    "conditionsApplicable",
    "decisionComments",
    "decisionDate",
    "decisionDocuments",
    "discardAllowed",
    "discardedDate",
    "documents",
    "eligibleBasicUdisForSscp",
    "eligibleCecps",
    "expiryDate",
    "humanTissues",
    "inVitroDiagnostics",
    "initialRegistration",
    "intendedMedicalPurpose",
    "issueDate",
    "ivdrMechanismOfScrutiny",
    "languages",
    "latestVersion",
    "manufacturer",
    "mechanismOfScrutinyEnabled",
    "mosAddedBudis",
    "mosOutsideEudamed",
    "new",
    "notifiedBody",
    "otherDecisionReasons",
    "precedingCertificates",
    "precedingCertificatesUnregistered",
    "producer",
    "qmsMosType",
    "revisionNumber",
    "scopes",
    "sppApplicable",
    "sppType",
    "sscpEnabled",
    "sscps",
    "startingDecisionApplicabilityDate",
    "startingValidityDate",
    "status",
    "statusChangeReasons",
    "sterile",
    "type",
    "ulid",
    "uuid",
    "versionDate",
    "versionNumber",
    "versionState"
  ],
  "document_example": {
    "uuid": "56fe9527-0928-40f9-8d1f-5074312e122d",
    "originalFileName": "Z-25-052-S-IX-E_qs_Airamed.pdf",
    "fileContentType": "application/pdf",
    "fileSize": 1691450,
    "tempFileName": null,
    "type": {
      "code": "refdata.document-type.certificate",
      "accessType": "PUBLIC"
    },
    "languages": [
      {
        "isoCode": "en",
        "name": "English"
      }
    ],
    "referenceDocId": null,
    "primaryModuleName": "CRF",
    "indexed": false,
    "virusCheck": 0,
    "new": false
  },
  "pdf_download_works": true
}
```

</details>

## ✅ Probe 07 — Offizielle Datalake-API

**Frage:** Kann die offizielle API mehr als PRIMARY_DI-Einzelabfragen?

**Ergebnis:** GEKLÄRT (12 Requests)

- Referenzaufruf -> ✅ HTTP 200, 4.6s.
- `value[]` enthält 1 Datensatz/Datensätze.
- Felder: `ACTIVE`, `ACTOR_ABBREVIATED_NAMES`, `ADMINISTERING_MEDICINE`, `ANIMAL_TISSUES`, `APPLICABLE_LEGISLATION_ID`, `AR_ACTOR_NAMES`, `AR_NAME`, `AR_SRN`, `BASIC_UDI`, `BASIC_UDI_DATA_ULID`, `BASIC_UDI_DATA_UUID`, `CMR_SUBSTANCE`, `COMPANION_DIAGNOSTICS`, `CONTAINER_PACKAGE_DIS`, `DEVICE_CRITERION`, `DEVICE_MODEL`, `DEVICE_NAME`, `DEVICE_STATUS_TYPE_ID`, `DIRECT_MARKETING_DI`, `ENDOCRINE_DISRUPTOR`, `HUMAN_PRODUCT`, `HUMAN_TISSUES`, `ID`, `IMPLANTABLE`, `INSTRUMENT`, `KIT`, `LATEST_VERSION`, `LATEX`, `MEASURING_FUNCTION`, `MEDICAL_PURPOSE`, `MEDICINAL_PRODUCT`, `MF_ACTOR_NAMES`, `MF_NAME`, `MF_SRN`, `MICROBIAL_SUBSTANCES`, `MULTI_COMPONENT_ID`, `NEAR_PATIENT_TESTING`, `NEW_DEVICE`, `NOMENCLATURE_CODE`, `PLACED_ON_THE_MARKET_ID` …
- ⚠️ Keine zertifikatsbezogenen Felder — für unseren Zweck damit unvollständig.
- Abruf ohne Filter -> ✅ 1000 Datensätze (Listenabruf möglich!)
- `CND_CODE=Q010601` -> ❌ HTTP 400
- `EMDN_CODE=Q010601` -> ❌ HTTP 400
- `cndCode=Q010601` -> ❌ HTTP 400
- `$filter=startswith(PRIMARY_DI,'E494')` -> ❌ HTTP 400
- `$top=5` -> ❌ HTTP 400
- `/certificate` -> ❌ HTTP 404
- `/certificates` -> ❌ HTTP 404
- `/actor` -> ❌ HTTP 404
- `/actors` -> ✅ existiert
- `/device` -> ❌ HTTP 404
- **Fazit:** Nur Einzelabfragen über PRIMARY_DI — für die Produktgruppen-Suche unbrauchbar. Es bleibt bei der UI-API. Bei künftigen EUDAMED-Releases erneut prüfen.

<details><summary>Rohdaten</summary>

```json
{
  "udi_fields": [
    "ACTIVE",
    "ACTOR_ABBREVIATED_NAMES",
    "ADMINISTERING_MEDICINE",
    "ANIMAL_TISSUES",
    "APPLICABLE_LEGISLATION_ID",
    "AR_ACTOR_NAMES",
    "AR_NAME",
    "AR_SRN",
    "BASIC_UDI",
    "BASIC_UDI_DATA_ULID",
    "BASIC_UDI_DATA_UUID",
    "CMR_SUBSTANCE",
    "COMPANION_DIAGNOSTICS",
    "CONTAINER_PACKAGE_DIS",
    "DEVICE_CRITERION",
    "DEVICE_MODEL",
    "DEVICE_NAME",
    "DEVICE_STATUS_TYPE_ID",
    "DIRECT_MARKETING_DI",
    "ENDOCRINE_DISRUPTOR",
    "HUMAN_PRODUCT",
    "HUMAN_TISSUES",
    "ID",
    "IMPLANTABLE",
    "INSTRUMENT",
    "KIT",
    "LATEST_VERSION",
    "LATEX",
    "MEASURING_FUNCTION",
    "MEDICAL_PURPOSE",
    "MEDICINAL_PRODUCT",
    "MF_ACTOR_NAMES",
    "MF_NAME",
    "MF_SRN",
    "MICROBIAL_SUBSTANCES",
    "MULTI_COMPONENT_ID",
    "NEAR_PATIENT_TESTING",
    "NEW_DEVICE",
    "NOMENCLATURE_CODE",
    "PLACED_ON_THE_MARKET_ID",
    "PRIMARY_DI",
    "PROFESSIONAL_TESTING",
    "REAGENT",
    "REFERENCE",
    "REPROCESSED",
    "REUSABLE",
    "RISK_CLASS_ID",
    "SECONDARY_DI",
    "SELF_TESTING",
    "SPECIAL_DEVICE_TYPE_ID",
    "STATUS_ID",
    "STERILE",
    "STERILIZATION",
    "SUBSTATUSES",
    "SUTURES",
    "TRADE_NAME",
    "UDI_DI_DATA_ULID",
    "UNIT_OF_USE_DI",
    "UUID",
    "VERSION_NUMBER"
  ],
  "cert_fields": [],
  "reference_call": "ok",
  "listing": true,
  "group_params": {
    "CND_CODE": "✗ HTTP 400",
    "EMDN_CODE": "✗ HTTP 400",
    "cndCode": "✗ HTTP 400",
    "$filter": "✗ HTTP 400",
    "$top": "✗ HTTP 400"
  },
  "siblings": {
    "/certificate": "✗ HTTP 404",
    "/certificates": "✗ HTTP 404",
    "/actor": "✗ HTTP 404",
    "/actors": "✓ HTTP 200",
    "/device": "✗ HTTP 404"
  }
}
```

</details>
