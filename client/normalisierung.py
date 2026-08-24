"""Translate official Datalake records into the shape of the UI API.

    from client.normalisierung import als_suchtreffer, als_detail

    treffer = [als_suchtreffer(satz) for satz in saetze]
    detail = als_detail(satz)

Both APIs describe the same devices, but with different field names, types and
encodings. After translation a record from either source can be consumed
through a single schema.

## Four differences that have to be translated

**1. Spelling.** `MF_SRN` vs `manufacturerSrn`, `TRADE_NAME` vs `tradeName`.

**2. Booleans are floats.** `IMPLANTABLE = 0.0`, `REUSABLE = 1.0`, and `null`
means "not stated". `_flagge()` maps them to False/True/None; "explicitly no"
stays distinguishable from "nothing entered".

**3. Enumerations are opaque negative ids.** `RISK_CLASS_ID = -203.0` means
class I. `/reference` resolves the ids, but only as a **translation** ("Class
I", "Classe I", "Κατηγορία I"), not as a stable identifier. The tables below
are therefore hard-coded: they are small (13 risk classes, 7 legislations, 3
market statuses), read completely from `/reference` on 2026-08-17, and
`watch/apiwacht.py` checks whether they still hold. Deriving the mapping from
translations at runtime would be guessing.

**4. Multilingual fields are JSON inside a string.** `MF_ACTOR_NAMES` carries
`'{"texts": [{"language": …, "text": …}]}'` as a string, not as an object.
`_mehrsprachig()` unpacks it.

## What the official API does NOT provide

Gaps that remain after translation, and that must not be read as "not stated
in EUDAMED":

    country/market data   only the UI detail call carries it
    single use            no field in the official record
    version date          no field
    certificates          not at all — the official API does not carry them

`als_detail()` therefore leaves these fields unset instead of filling them with
a substitute value, so a later UI call can supply them.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Source tag for records taken from the official API.
QUELLE = "offiziell"

# ---------------------------------------------------------------------------
# The enumeration tables — read from /reference on 2026-08-17
# ---------------------------------------------------------------------------
#
# Complete: /reference lists 13, 7 and 3 values for these three fields, and all
# of them are here. An unmapped value becomes None, i.e. "not stated" — never a
# guessed neighbour.

#: `RISK_CLASS_ID` -> the `refdata.risk-class.*` codes of the UI API.
#:
#: The legacy values (AIMDD, IVD Annex II, IVD general) have no counterpart in
#: today's `refdata.risk-class.*`; they come from the predecessor directives
#: and stay unmapped rather than being assigned an approximate class.
RISIKOKLASSEN: dict[float, str] = {
    -203.0: "refdata.risk-class.class-i",
    -204.0: "refdata.risk-class.class-iia",
    -205.0: "refdata.risk-class.class-iib",
    -10.0: "refdata.risk-class.class-iii",
    -199.0: "refdata.risk-class.class-a",
    -200.0: "refdata.risk-class.class-b",
    -201.0: "refdata.risk-class.class-c",
    -202.0: "refdata.risk-class.class-d",
    # The only legacy value with a documented identifier: /reference leaves it
    # untranslated in the Hungarian row.
    -219.0: "refdata.risk-class.ivd-devices-self-testing",
}

#: `APPLICABLE_LEGISLATION_ID` -> `refdata.applicable-legislation.*`.
#: `-3020` ("None") and `-3021` ("Unknown") explicitly mean "not stated".
RECHTSRAHMEN: dict[float, str] = {
    -197.0: "refdata.applicable-legislation.mdr",
    -198.0: "refdata.applicable-legislation.ivdr",
    -53.0: "refdata.applicable-legislation.mdd",
    -54.0: "refdata.applicable-legislation.aimdd",
    -55.0: "refdata.applicable-legislation.ivdd",
}

#: `DEVICE_STATUS_TYPE_ID` -> `refdata.device-model-status.*`.
#: This is the only field for which `/reference` also carries the raw
#: identifiers, so the table is read rather than inferred.
MARKTSTATUS: dict[float, str] = {
    -11.0: "refdata.device-model-status.on-the-market",
    -12.0: "refdata.device-model-status.no-longer-on-the-market",
    -790.0: "refdata.device-model-status.not-intended-for-eu-market",
}

#: `SPECIAL_DEVICE_TYPE_ID` -> `refdata.special-device-type.*`. Only the two
#: software values are mapped; spectacles and contact lenses stay unmapped.
SONDERTYPEN: dict[float, str] = {
    -47.0: "refdata.special-device-type.software",
    -43.0: "refdata.special-device-type.software",
}


#: The inverse, for the FILTER direction: `class-iii` -> -10.0.
#:
#: `RISK_CLASS_ID` and `APPLICABLE_LEGISLATION_ID` do filter server-side, but
#: only with the API's own ids; the HTTP 400 applies to the VALUE
#: (`class-iii`), not to the parameter. Measured 2026-08-17 on one
#: manufacturer:
#:
#:     MF_SRN alone                     406
#:     + RISK_CLASS_ID=-10.0              4   (class III)
#:     + RISK_CLASS_ID=-203.0           235   (class I)
#:     + APPLICABLE_LEGISLATION_ID=-197 330   (MDR)
#:     + APPLICABLE_LEGISLATION_ID=-53   76   (MDD)  -> 330+76 = 406, exact
def _umkehr(tabelle: dict[float, str]) -> dict[str, float]:
    """Code -> numeric id. Both `refdata.risk-class.class-iii` and the short
    form `class-iii` resolve."""
    aus: dict[str, float] = {}
    for kennzahl, code in tabelle.items():
        aus[code] = kennzahl
        aus[code.rsplit(".", 1)[-1]] = kennzahl
    return aus


RISIKOKLASSEN_ID = _umkehr(RISIKOKLASSEN)
RECHTSRAHMEN_ID = _umkehr(RECHTSRAHMEN)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zahl(wert: Any) -> float | None:
    """The official record delivers every numeric value as a float."""
    try:
        return float(wert) if wert is not None else None
    except (TypeError, ValueError):
        return None


def _ganz(wert: Any) -> int | None:
    """`VERSION_NUMBER = 1.0` is the integer 1, not a decimal."""
    zahl = _zahl(wert)
    return int(zahl) if zahl is not None else None


def _flagge(wert: Any) -> bool | None:
    """0.0/1.0/null -> False/True/None.

    None stays None: "nothing entered" is not the same as "no".
    """
    zahl = _zahl(wert)
    return None if zahl is None else bool(zahl)


def _text(wert: Any) -> str | None:
    """Empty and whitespace-only strings count as not set."""
    if wert is None:
        return None
    gestutzt = str(wert).strip()
    return gestutzt or None


def _mehrsprachig(wert: Any) -> Any:
    """`'{"texts": [...]}'` as a string -> the parsed object.

    `MF_ACTOR_NAMES` and `ACTOR_ABBREVIATED_NAMES` arrive this way: JSON
    wrapped in a string. Passing it through unparsed stores the opening brace
    as the manufacturer name.
    """
    if not isinstance(wert, str):
        return wert
    gestutzt = wert.strip()
    if not gestutzt.startswith("{"):
        return gestutzt or None
    try:
        return json.loads(gestutzt)
    except json.JSONDecodeError:
        log.debug("Mehrsprachiges Feld nicht lesbar: %.60s", gestutzt)
        return None


#: Unmapped ids already reported once — keeps the log readable.
_GEMELDET: set[float] = set()


def _refdata(tabelle: dict[float, str], wert: Any) -> dict[str, str] | None:
    """Numeric id -> `{"code": "refdata…"}`, the form the UI API uses.

    An unknown value becomes None, **not** the nearest entry: a wrong risk
    class is worse than a missing one.
    """
    zahl = _zahl(wert)
    if zahl is None:
        return None
    code = tabelle.get(zahl)
    if code is None:
        # Once per id, not once per record: 406 devices sharing the same
        # unmapped special type would otherwise log 406 identical lines.
        if zahl not in _GEMELDET:
            _GEMELDET.add(zahl)
            log.info("Referenzkennzahl %s ist nicht abgebildet — bleibt leer "
                     "(siehe die Tabellen oben; unbekannt heißt hier "
                     "„keine Angabe“, nie „nächstbester Wert“)", zahl)
        return None
    return {"code": code}


# ---------------------------------------------------------------------------
# The transformations
# ---------------------------------------------------------------------------


def als_suchtreffer(satz: dict[str, Any]) -> dict[str, Any]:
    """Official record -> the shape of a `/devices/udiDiData` search hit.

    Two field names differ on the official side: `ulid` is `UDI_DI_DATA_ULID`,
    `basicUdiDiDataUlid` is `BASIC_UDI_DATA_ULID`. `versionState` has no
    official counterpart and stays None.
    """
    return {
        "uuid": _text(satz.get("UUID")),
        "ulid": _text(satz.get("UDI_DI_DATA_ULID")),
        "primaryDi": _text(satz.get("PRIMARY_DI")),
        "basicUdi": _text(satz.get("BASIC_UDI")),
        "basicUdiDiDataUlid": _text(satz.get("BASIC_UDI_DATA_ULID")),
        "tradeName": _text(satz.get("TRADE_NAME")),
        "manufacturerName": (_text(satz.get("MF_NAME"))
                             or _mehrsprachig(satz.get("MF_ACTOR_NAMES"))),
        "manufacturerSrn": _text(satz.get("MF_SRN")),
        "authorisedRepresentativeName": (
            _text(satz.get("AR_NAME")) or _mehrsprachig(satz.get("AR_ACTOR_NAMES"))),
        "authorisedRepresentativeSrn": _text(satz.get("AR_SRN")),
        "riskClass": _refdata(RISIKOKLASSEN, satz.get("RISK_CLASS_ID")),
        "deviceStatusType": _refdata(MARKTSTATUS, satz.get("DEVICE_STATUS_TYPE_ID")),
        # No official `versionState` — `STATUS_ID` is the active state of the
        # record and means something else.
        "versionState": None,
        "latestVersion": _flagge(satz.get("LATEST_VERSION")),
        "versionNumber": _ganz(satz.get("VERSION_NUMBER")),
    }


#: `ACTOR_TYPE` (clear text) -> the refdata code used by the UI API.
#:
#: The official API spells the role out ("Manufacturer"), the UI API uses a
#: code.
#:
#: The dump carries three further roles that are deliberately MISSING here:
#: "Notified Body", "Competent Authority" and "European Commission" (measured
#: 2026-08-19, together three records in two thousand). Their refdata codes do
#: not appear in any measured source, so no mapping is asserted; such records
#: keep their clear-text role and get no code.
AKTEURSROLLEN: dict[str, str] = {
    "manufacturer": "refdata.actor-type.manufacturer",
    "authorised representative": "refdata.actor-type.authorised-representative",
    "importer": "refdata.actor-type.importer",
    "system or procedure pack producer":
        "refdata.actor-type.system-procedure-pack-producer",
    "system/procedure pack producer":
        "refdata.actor-type.system-procedure-pack-producer",
}

#: Fields of the official actor dump that are NOT carried over.
#:
#: `PRRC_FIRST_NAME` and `PRRC_FAMILY_NAME` name the person responsible for
#: regulatory compliance — personal data. Everything else in the dump
#: describes a company. The names are dropped on read rather than stored and
#: hidden later.
NICHT_UEBERNOMMEN = frozenset({"PRRC_FIRST_NAME", "PRRC_FAMILY_NAME"})


def als_akteur(satz: dict[str, Any]) -> dict[str, Any]:
    """Official actor record -> the shape of a UI-API actor search hit.

    The official dump is the richer of the two: it carries `STATUS`
    (active/inactive), `STATUS_FROM_DATE` and `VERSION`, plus website and VAT
    number, which the UI API exposes only in the per-actor detail call.

    The status matters: one measured manufacturer has seven registrations, of
    which exactly **one** is active (SE-MF-000002125, measured 2026-08-19).
    Without the status the six deregistered ones look like equal siblings.

    `PRRC_*` is dropped, see `NICHT_UEBERNOMMEN`.
    """
    rolle = _text(satz.get("ACTOR_TYPE")) or ""
    return {
        "eudamedIdentifier": _text(satz.get("ACTOR_ID")),
        # The official API carries no UUID. It stays None so that a UUID
        # already known from the UI API is not overwritten.
        "uuid": None,
        "name": _text(satz.get("NAME")),
        "abbreviatedName": _text(satz.get("ABBREVIATED_NAME")),
        "countryIso2Code": (_text(satz.get("ACT_COUNTRY_ISO2_CODE"))
                            or _text(satz.get("ACT_ADDR_COUNTRY_CODE"))),
        "countryName": (_text(satz.get("ACT_COUNTRY_NAME"))
                        or _text(satz.get("ACT_ADDR_COUNTRY_NAME"))),
        "actorType": {"code": AKTEURSROLLEN[rolle.strip().lower()]}
                     if rolle.strip().lower() in AKTEURSROLLEN else None,
        "roleName": rolle or None,
        # The dump has no registration date; `STATUS_FROM_DATE` is the start of
        # the CURRENT status and means something else.
        "dateOfRegistration": None,
        "actorStatus": {"code": _statuscode(satz.get("STATUS"))}
                       if _text(satz.get("STATUS")) else None,
        "actorStatusFromDate": _text(satz.get("STATUS_FROM_DATE")),
        "versionNumber": _ganz(satz.get("VERSION")),
        "electronicMail": _text(satz.get("ACT_EMAIL")),
        "telephone": _text(satz.get("ACT_TELEPHONE")),
        "website": _text(satz.get("ACT_WEBSITE")),
        "vatNumber": _text(satz.get("EUROPEAN_VAT_NUMBER")),
        "cityName": _text(satz.get("ACT_ADDR_CITY_NAME")),
        "postalZone": _text(satz.get("ACT_ADDR_POSTAL_ZONE")),
        "geographicalAddress": _anschrift(satz),
    }


def _statuscode(wert: Any) -> str:
    """Maps "Active" to `refdata.actor-status.active`; unknown stays raw."""
    kurz = (_text(wert) or "").strip().lower()
    return (f"refdata.actor-status.{kurz}"
            if kurz in ("active", "inactive") else kurz)


def _anschrift(satz: dict[str, Any]) -> str | None:
    """Join the address parts into the single line the UI API returns.

    Order as in the UI API: street, building number, postal code, city.
    Missing parts are omitted rather than left as empty separators.
    """
    teile = [
        _text(satz.get("ACT_ADDR_STREET_NAME")),
        _text(satz.get("ACT_ADDR_BUILDING_NUMBER")),
        _text(satz.get("ACT_ADDR_POSTAL_ZONE")),
        _text(satz.get("ACT_ADDR_CITY_NAME")),
    ]
    zusammen = " ".join(t.strip() for t in teile if t and t.strip())
    return zusammen or None


def als_detail(satz: dict[str, Any]) -> dict[str, Any]:
    """Official record -> the shape of `/devices/udiDiData/{uuid}`.

    These fields cost **one request per device** on the UI API; the official
    search returns them inline.

    `cndNomenclatures` holds at most one entry because the official record
    carries exactly one `NOMENCLATURE_CODE`. The real assignment is
    multi-valued: 19 of 1,183 devices in one sample (1.6 %, measured
    2026-08-17) have two to four codes, so this list may be incomplete.

    `marketInfoLink` is absent: the official API has no country data.
    """
    code = _text(satz.get("NOMENCLATURE_CODE"))
    return {
        "primaryDi": {"code": _text(satz.get("PRIMARY_DI"))},
        "tradeName": _text(satz.get("TRADE_NAME")),
        "additionalDescription": _text(satz.get("DEVICE_NAME")),
        "deviceStatus": {
            "type": _refdata(MARKTSTATUS, satz.get("DEVICE_STATUS_TYPE_ID"))},
        "latestVersion": _flagge(satz.get("LATEST_VERSION")),
        "versionNumber": _ganz(satz.get("VERSION_NUMBER")),
        "sterile": _flagge(satz.get("STERILE")),
        # No `SINGLE_USE` in the official record — omitted rather than
        # guessed, so a later UI value can supply it.
        "cndNomenclatures": [{"code": code}] if code else [],
    }


def als_basic_udi(satz: dict[str, Any]) -> dict[str, Any]:
    """Official record -> the shape of `/devices/basicUdiData/…`.

    Carries the device properties and the legislation, but
    `deviceCertificateInfoList` is always empty: the official API has no
    certificates at all. Callers must not read that empty list as "queried,
    none found" — only the UI API can answer that question.
    """
    return {
        "deviceName": _text(satz.get("DEVICE_NAME")),
        "deviceModel": _text(satz.get("DEVICE_MODEL")),
        "riskClass": _refdata(RISIKOKLASSEN, satz.get("RISK_CLASS_ID")),
        "legislation": _refdata(RECHTSRAHMEN,
                                satz.get("APPLICABLE_LEGISLATION_ID")),
        "specialDeviceType": _refdata(SONDERTYPEN,
                                      satz.get("SPECIAL_DEVICE_TYPE_ID")),
        "implantable": _flagge(satz.get("IMPLANTABLE")),
        "active": _flagge(satz.get("ACTIVE")),
        "reusable": _flagge(satz.get("REUSABLE")),
        "deviceCertificateInfoList": [],
    }
