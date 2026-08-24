"""Probe 06 — certificate search: real field names and enum values.

Questions:
  a) What are the actual field names of the certificate search? Published lists
     derive from PowerShell `select` statements, and PowerShell is
     case-insensitive, so their spelling is guesswork.
  b) Is the expiry date called `expiryDate` here and `certificateExpiry` in the
     Basic UDI detail? A client then has to reconcile the two names.
  c) Which values do `certificateType` and `status` take?
  d) Does the route to the original PDF work (search -> detail C3 ->
     `documents[]` -> PDF C4, see `client/eudamed_client.py`)?
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from client import EudamedClient
from probes.base import ProbeResult, Verdict, call, non_null_keys, null_keys

PROBE_ID = "06"
TITLE = "Zertifikatssuche: Feldnamen, Enums, PDF-Pfad"
QUESTION = "Wie sehen die Daten der Zertifikatssuche wirklich aus?"

#: TÜV Rheinland, used as the reference notified body.
REFERENCE_NB_SRN = "0197"


def _collect_codes(entries: list[dict[str, Any]], field: str) -> Counter:
    counter: Counter = Counter()
    for entry in entries:
        value = entry.get(field)
        if isinstance(value, dict):
            value = value.get("code")
        if value:
            counter[str(value)] += 1
    return counter


def run(client: EudamedClient) -> ProbeResult:
    result = ProbeResult(PROBE_ID, TITLE, QUESTION)

    search, error = call(client.search_certificates, page=0, page_size=100)
    result.requests_made += 1
    if error or search is None:
        result.conclude(Verdict.ERROR, f"Zertifikatssuche nicht erreichbar: {error}")
        return result

    entries = search.content
    result.add(f"Gesamtzahl Zertifikate: **{search.total_elements}**, geladen: {len(entries)}.")
    if not entries:
        result.conclude(Verdict.PARTIAL, "Endpunkt antwortet, liefert aber keine Treffer.")
        return result

    first = entries[0]
    result.data["certificate_search_entry"] = first
    result.add(f"Felder (befüllt): `{'`, `'.join(non_null_keys(first))}`")
    if null_keys(first):
        result.add(f"Felder (leer): `{'`, `'.join(null_keys(first))}`")

    # (b) What is the expiry date field called here?
    date_fields = [k for k in first if any(t in k.lower() for t in ("date", "expiry", "valid"))]
    result.add(f"Datumsfelder: `{'`, `'.join(sorted(date_fields))}`")
    result.data["date_fields"] = sorted(date_fields)
    if "expiryDate" in first and "certificateExpiry" not in first:
        result.add("-> Bestätigt: hier `expiryDate`, im Basic-UDI-Detail `certificateExpiry`. "
                   "Im SQLite-Mapping auf `expiry_date` vereinheitlichen.")

    # (c) Collect enum values
    for field in ("certificateType", "status", "certificateStatus", "type"):
        codes = _collect_codes(entries, field)
        if codes:
            result.add(f"`{field}` — beobachtete Werte: "
                       + ", ".join(f"`{code}` ({n}x)" for code, n in codes.most_common()))
            result.data[f"codes_{field}"] = dict(codes)

    # Check the notified-body filter
    filtered, err_filtered = call(
        client.search_certificates, notified_body_srn=REFERENCE_NB_SRN, page=0, page_size=1
    )
    result.requests_made += 1
    if err_filtered or filtered is None:
        result.add(f"`notifiedBodySrn={REFERENCE_NB_SRN}` -> Fehler: {err_filtered}")
    else:
        works = filtered.total_elements != search.total_elements
        result.add(
            f"`notifiedBodySrn={REFERENCE_NB_SRN}` -> {filtered.total_elements} von "
            f"{search.total_elements} Zertifikaten ({'filtert' if works else '⚠️ wirkt nicht'})."
        )
        result.data["nb_filter_works"] = works

    # (d) Detail and document path
    cert_uuid = first.get("uuid")
    documents: list[dict[str, Any]] = []
    if cert_uuid:
        detail, err_detail = call(client.get_certificate, cert_uuid)
        result.requests_made += 1
        if err_detail or detail is None:
            result.add(f"Zertifikat-Detail nicht abrufbar: {err_detail}")
        else:
            payload = detail.data if isinstance(detail.data, dict) else {}
            result.data["certificate_detail_keys"] = sorted(payload.keys())
            result.add(f"Detail-Felder: `{'`, `'.join(sorted(payload.keys()))}`")
            documents = payload.get("documents") or []
            result.add(f"`documents[]`: {len(documents)} Eintrag/Einträge.")
            if documents:
                result.add(f"Dokument-Felder: `{'`, `'.join(sorted(documents[0].keys()))}`")
                result.data["document_example"] = documents[0]

    if documents:
        doc_uuid = documents[0].get("uuid")
        if doc_uuid:
            download, err_download = call(client.download_document, doc_uuid)
            result.requests_made += 1
            if err_download or download is None:
                result.add(f"PDF-Download -> Fehler: {err_download}")
                result.data["pdf_download_works"] = False
            else:
                blob = download.data if isinstance(download.data, bytes) else b""
                is_pdf = blob[:4] == b"%PDF"
                result.add(
                    f"PDF-Download `{documents[0].get('originalFilename')}` -> "
                    f"{len(blob)} Bytes, {'gültiges PDF' if is_pdf else '⚠️ kein PDF-Header'}."
                )
                result.data["pdf_download_works"] = is_pdf

    result.conclude(
        Verdict.RESOLVED,
        "Feldnamen und Enum-Werte der Zertifikatssuche sind jetzt belegt (siehe oben).",
    )
    return result
