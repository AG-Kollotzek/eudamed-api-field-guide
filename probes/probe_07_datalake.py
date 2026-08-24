"""Probe 07 — can the official Datalake API do more than single lookups?

Question: the official API

    https://api.datalake.sante.service.ec.europa.eu/eudamed/udi?PRIMARY_DI=...&format=json&api-version=v1.0

is only known to support access by `PRIMARY_DI`. If it also supported group
queries it would be a considerably more stable basis than the unofficial UI
API.

Tested: the documented single lookup, a request without filters, an EMDN filter
under several plausible parameter names, OData `$filter`/`$top`, and a few
adjacent resources. Everything beyond the documented lookup is guessed: a zero
result says nothing about the API, only about the guessed name.
"""

from __future__ import annotations

from typing import Any

from client import EudamedClient
from client.eudamed_client import DATALAKE_URL
from probes.base import ProbeResult, Verdict, call

PROBE_ID = "07"
TITLE = "Offizielle Datalake-API"
QUESTION = "Kann die offizielle API mehr als PRIMARY_DI-Einzelabfragen?"

REFERENCE_PRIMARY_DI = "E4947662361"
API_VERSION = "v1.0"


def _get(client: EudamedClient, path: str, params: dict[str, Any]) -> tuple[Any, str | None]:
    merged = {"format": "json", "api-version": API_VERSION, **params}
    response, error = call(client.request, path, merged, base=DATALAKE_URL)
    return response, error


def run(client: EudamedClient) -> ProbeResult:
    result = ProbeResult(PROBE_ID, TITLE, QUESTION)

    # --- Reference case: does the documented call work at all? ------------------
    response, error = _get(client, "/udi", {"PRIMARY_DI": REFERENCE_PRIMARY_DI})
    result.requests_made += 1
    if error or response is None:
        result.add(f"Referenzaufruf `PRIMARY_DI={REFERENCE_PRIMARY_DI}` -> ❌ {error}")
        result.data["reference_call"] = f"Fehler: {error}"
        if error and ("401" in error or "403" in error):
            result.conclude(
                Verdict.RESOLVED,
                "Die offizielle API verlangt inzwischen Authentifizierung (Subscription Key "
                "über das Developer-Portal). Für den Prototyp bleibt es bei der UI-API.",
            )
            return result
    else:
        payload = response.data
        values = payload.get("value") if isinstance(payload, dict) else None
        result.add(f"Referenzaufruf -> ✅ HTTP 200, {response.elapsed_s:.1f}s.")
        if isinstance(values, list):
            result.add(f"`value[]` enthält {len(values)} Datensatz/Datensätze.")
            if values and isinstance(values[0], dict):
                keys = sorted(values[0].keys())
                result.data["udi_fields"] = keys
                result.add(f"Felder: `{'`, `'.join(keys[:40])}`"
                           + (" …" if len(keys) > 40 else ""))
                cert_fields = [k for k in keys if "cert" in k.lower()]
                result.add(
                    f"Zertifikatsbezogene Felder: `{'`, `'.join(cert_fields)}`"
                    if cert_fields else
                    "⚠️ Keine zertifikatsbezogenen Felder — für unseren Zweck damit unvollständig."
                )
                result.data["cert_fields"] = cert_fields
        result.data["reference_call"] = "ok"

    # --- Does it work without a filter (plain listing)? -------------------------
    listing, err_listing = _get(client, "/udi", {})
    result.requests_made += 1
    if err_listing or listing is None:
        result.add(f"Abruf ohne Filter -> ❌ {err_listing} (kein freier Listenabruf)")
        result.data["listing"] = False
    else:
        values = listing.data.get("value") if isinstance(listing.data, dict) else None
        count = len(values) if isinstance(values, list) else "?"
        result.add(f"Abruf ohne Filter -> ✅ {count} Datensätze (Listenabruf möglich!)")
        result.data["listing"] = True

    # --- Is there a group / EMDN filter? ----------------------------------------
    group_params = [
        {"CND_CODE": "Q010601"},
        {"EMDN_CODE": "Q010601"},
        {"cndCode": "Q010601"},
        {"$filter": "startswith(PRIMARY_DI,'E494')"},
        {"$top": "5"},
    ]
    group_findings: dict[str, str] = {}
    for params in group_params:
        name = next(iter(params))
        response, error = _get(client, "/udi", params)
        result.requests_made += 1
        if error or response is None:
            group_findings[name] = f"✗ {error}"
            result.add(f"`{name}={params[name]}` -> ❌ {error}")
        else:
            values = response.data.get("value") if isinstance(response.data, dict) else None
            count = len(values) if isinstance(values, list) else 0
            group_findings[name] = f"✓ {count} Datensätze"
            result.add(f"`{name}={params[name]}` -> ✅ {count} Datensätze")
    result.data["group_params"] = group_findings

    # --- Adjacent resources -----------------------------------------------------
    sibling_findings: dict[str, str] = {}
    for path in ("/certificate", "/certificates", "/actor", "/actors", "/device"):
        response, error = _get(client, path, {})
        result.requests_made += 1
        sibling_findings[path] = f"✗ {error}" if error else "✓ HTTP 200"
        result.add(f"`{path}` -> {'❌ ' + str(error) if error else '✅ existiert'}")
    result.data["siblings"] = sibling_findings

    usable = any(v.startswith("✓") for v in group_findings.values())
    has_certs = bool(result.data.get("cert_fields"))
    has_cert_resource = any(v == "✓ HTTP 200" for k, v in sibling_findings.items() if "cert" in k)

    if usable and (has_certs or has_cert_resource):
        result.conclude(
            Verdict.RESOLVED,
            "Die offizielle API beherrscht Gruppenabfragen **und** liefert Zertifikatsdaten. "
            "Ernsthaft als primäre Quelle prüfen statt der UI-API.",
        )
    elif usable:
        result.conclude(
            Verdict.PARTIAL,
            "Gruppenabfragen sind möglich, Zertifikatsdaten aber nicht erkennbar. "
            "Als Ergänzung interessant, nicht als Ersatz.",
        )
    else:
        result.conclude(
            Verdict.RESOLVED,
            "Nur Einzelabfragen über PRIMARY_DI — für die Produktgruppen-Suche unbrauchbar. "
            "Es bleibt bei der UI-API. Bei künftigen EUDAMED-Releases erneut prüfen.",
        )
    return result
