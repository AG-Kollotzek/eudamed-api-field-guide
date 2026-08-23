"""Probe 04 — Wie kommt man vom Suchtreffer zum Zertifikat? (KRITISCH)

Frage: Wie erreicht man die gerätegenauen Zertifikatsdaten von einem Suchtreffer aus?

GELÖST am 2026-07-30 durch Mitschnitt des EUDAMED-UI-Traffics:

    GET /devices/basicUdiData/udiDiData/{deviceUuid}

Man übergibt die **Geräte-UUID** aus dem Suchergebnis, keine separate Basic-UDI-ID.
Die `basicUdiDiDataUlid` wird dafür gar nicht gebraucht. Die openregulatory-Spec nennt
`/devices/basicUdiData/{basicUdiDiId}` — dieser Pfad existiert zwar (er antwortet mit
404 statt mit einem Redirect), ist aber über keine ID aus dem Suchergebnis erreichbar.

Diese Probe hält das fest und misst zusätzlich die **Datenabdeckung**: wie viele
Geräte haben überhaupt hinterlegte Zertifikate? Das ist für die Auswertung wichtiger
als der Endpunkt selbst — ein leeres `deviceCertificateInfoList` heißt nicht
"unzertifiziert", sondern "vom Hersteller nicht eingetragen".
"""

from __future__ import annotations

from collections import Counter

from client import EudamedClient
from probes.base import ProbeResult, Verdict, call, id_kind, non_null_keys, null_keys

PROBE_ID = "04"
TITLE = "Weg vom Suchtreffer zum Zertifikat"
QUESTION = "Wie erreicht man deviceCertificateInfoList von einem Suchtreffer aus?"

REFERENCE_CND = "Q010601"
#: So viele Geräte für die Abdeckungsmessung. Jedes kostet einen Request.
SAMPLE_SIZE = 12


def run(client: EudamedClient) -> ProbeResult:
    result = ProbeResult(PROBE_ID, TITLE, QUESTION)

    search, error = call(client.search_devices, cnd_code=REFERENCE_CND, page=0, page_size=SAMPLE_SIZE)
    result.requests_made += 1
    if error or search is None or not search.content:
        result.conclude(Verdict.ERROR, f"Keine Treffer für cndCode={REFERENCE_CND}: {error}")
        return result

    entry = search.content[0]
    device_uuid = entry.get("uuid")
    result.data["search_entry"] = entry
    result.add(f"Suchtreffer-Felder (befüllt): `{'`, `'.join(non_null_keys(entry))}`")
    result.add(f"Suchtreffer-Felder (leer): `{'`, `'.join(null_keys(entry))}`")
    result.add(
        f"`uuid` = `{device_uuid}` ({id_kind(device_uuid)}), "
        f"`basicUdiDiDataUlid` = `{entry.get('basicUdiDiDataUlid')}` "
        f"({id_kind(entry.get('basicUdiDiDataUlid'))}), "
        f"`basicUdiDataUuid` = `{entry.get('basicUdiDataUuid')}`"
    )

    # --- Der Weg, den die UI geht ----------------------------------------------
    working, err_working = call(client.get_basic_udi_by_device, device_uuid)
    result.requests_made += 1
    if err_working or working is None:
        result.conclude(
            Verdict.OPEN,
            f"`/devices/basicUdiData/udiDiData/{{deviceUuid}}` -> {err_working}. "
            "Der 2026-07-30 gefundene Weg funktioniert nicht mehr — UI-Traffic erneut mitschneiden.",
        )
        return result

    payload = working.data if isinstance(working.data, dict) else {}
    result.add(f"✅ `/devices/basicUdiData/udiDiData/{{deviceUuid}}` -> HTTP 200, "
               f"{len(payload)} Felder.")
    result.data["basic_udi_keys"] = sorted(payload.keys())

    for key in ("deviceName", "deviceModel"):
        if payload.get(key):
            result.add(f"`{key}`: `{payload[key]}`")
    for key in ("riskClass", "legislation"):
        value = payload.get(key)
        if isinstance(value, dict):
            result.add(f"`{key}.code`: `{value.get('code')}`")

    # --- Gegenprobe: der Pfad aus der openregulatory-Spec ------------------------
    ulid = entry.get("basicUdiDiDataUlid")
    if ulid:
        _, err_spec = call(
            client.request, f"/devices/basicUdiData/{ulid}", {"languageIso2Code": "en"}
        )
        result.requests_made += 1
        result.add(
            f"Gegenprobe `/devices/basicUdiData/{{basicUdiDiDataUlid}}` (Pfad laut "
            f"openregulatory-Spec) -> {err_spec or 'HTTP 200'}"
        )
        result.data["spec_path_error"] = err_spec

    # --- Datenabdeckung ---------------------------------------------------------
    with_certs = 0
    total_certs = 0
    type_codes: Counter = Counter()
    status_codes: Counter = Counter()
    example = None
    coverage_rows = []

    for hit in search.content:
        certs, err_cert = call(client.get_device_certificates, hit.get("uuid"))
        result.requests_made += 1
        if err_cert or certs is None:
            continue
        if certs:
            with_certs += 1
            total_certs += len(certs)
            example = example or certs[0]
            for cert in certs:
                if code := (cert.get("certificateType") or {}).get("code"):
                    type_codes[code] += 1
                if code := (cert.get("status") or {}).get("code"):
                    status_codes[code] += 1
        coverage_rows.append({
            "manufacturer": hit.get("manufacturerName"),
            "riskClass": (hit.get("riskClass") or {}).get("code", ""),
            "certs": len(certs),
        })

    sample = len(coverage_rows)
    result.data["coverage"] = coverage_rows
    result.add(
        f"**Datenabdeckung:** {with_certs} von {sample} Geräten haben Zertifikatsdaten "
        f"({total_certs} Zertifikate insgesamt)."
    )

    if example:
        result.data["certificate_example"] = example
        result.add(f"Zertifikatsfelder: `{'`, `'.join(sorted(example.keys()))}`")
        result.add(
            f"Beispiel: Nr `{example.get('certificateNumber')}`, "
            f"Ablauf `{example.get('certificateExpiry')}`, "
            f"NB `{(example.get('notifiedBody') or {}).get('name')}` "
            f"(SRN `{(example.get('notifiedBody') or {}).get('srn')}`)"
        )
        result.data["certificate_type_codes"] = dict(type_codes)
        result.data["certificate_status_codes"] = dict(status_codes)
        result.add(f"`certificateType.code`: {dict(type_codes) or '—'}")
        result.add(f"`status.code`: {dict(status_codes) or '—'}")

    if with_certs == 0:
        result.conclude(
            Verdict.PARTIAL,
            "Endpunkt erreichbar, aber in dieser Stichprobe hatte kein Gerät "
            "Zertifikatsdaten. Mit anderer Produktgruppe gegenprüfen.",
        )
        return result

    result.conclude(
        Verdict.RESOLVED,
        f"Gerätegenaue Zertifikate sind über `/devices/basicUdiData/udiDiData/{{deviceUuid}}` "
        f"erreichbar. Der Fallback über `actorSrn` wird nicht gebraucht. "
        f"**Aber:** nur {with_certs}/{sample} Geräte haben überhaupt Daten — ein leeres "
        "Ergebnis heißt 'nicht eingetragen', nicht 'unzertifiziert'. Das muss die "
        "Auswertung unterscheidbar machen.",
    )
    return result
