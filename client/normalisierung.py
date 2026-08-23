"""Offizielle Datensätze in die Form bringen, die `db/store.py` schon kennt.

    from client.normalisierung import als_suchtreffer, als_detail

    store.upsert_devices_from_search([als_suchtreffer(s) for s in saetze])
    store.upsert_device_detail(uuid, als_detail(satz))

Damit gilt die Auflage „einheitliches Datenschema" wörtlich: **dieselben
Upserts, dieselben Tabellen, dieselben Spalten.** Es gibt keinen zweiten
Speicherweg und keine Parallelfelder — eine offiziell geholte Zeile ist von
einer über die UI geholten nur an ihrer `quelle` zu unterscheiden, nicht an
ihrem Aufbau.

## Vier Unterschiede, die übersetzt werden müssen

**1. Schreibweise.** `MF_SRN` gegen `manufacturerSrn`, `TRADE_NAME` gegen
`tradeName`. Stumpfe Abbildung, unten in `_FELDER`.

**2. Wahrheitswerte sind Fließkommazahlen.** `IMPLANTABLE = 0.0`, `REUSABLE =
1.0`, und `null` heißt „keine Angabe". `_flagge()` macht daraus 0/1/None —
wobei die Unterscheidung zwischen 0 und None hier genauso trägt wie im
restlichen Projekt: „ausdrücklich nein" ist etwas anderes als „nichts
eingetragen".

**3. Aufzählungswerte sind undurchsichtige negative Kennzahlen.**
`RISK_CLASS_ID = -203.0` bedeutet Klasse I. Die Zuordnung steht in `/reference`
— aber dort nur als **Übersetzung** („Class I", „Classe I", „Κατηγορία I"),
nicht als Bezeichner. Deshalb stehen die Tabellen unten fest im Code: Sie sind
klein (13 Risikoklassen, 7 Rechtsrahmen, 3 Marktstatus), am 2026-08-17
vollständig aus `/reference` abgelesen, und `apiwacht.py` sieht nach, ob sie
noch stimmen. Eine zur Laufzeit aus Übersetzungen geratene Zuordnung wäre in
einem Zulassungsumfeld die falsche Sorte Bequemlichkeit.

**4. Mehrsprachige Felder sind JSON in einem String.** `MF_ACTOR_NAMES` trägt
`'{"texts": [{"language": …, "text": …}]}'` — als Zeichenkette, nicht als
Objekt. `_mehrsprachig()` packt es aus, damit `store._text()` es wie gewohnt
behandeln kann.

## Was die offizielle Schnittstelle NICHT liefert

Auch nach der Übersetzung bleiben Lücken, und sie gehören benannt, weil sie
sonst als „keine Angabe in EUDAMED" durchgingen:

    device_markets     die Länderangaben — die füllt nur der UI-Detailabruf
    single_use         kein Feld im offiziellen Datensatz
    version_date       kein Feld
    Zertifikate        gar nicht — die offizielle API führt sie nicht

`als_detail()` schreibt diese Felder deshalb **nicht** — es lässt sie leer,
statt sie mit einem Ersatzwert zu belegen. Die Upserts arbeiten durchgehend mit
`COALESCE`, ein späterer UI-Abruf ergänzt sie also, ohne das Vorhandene zu
überschreiben.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Die Quellenkennung, die in `devices.quelle` landet.
QUELLE = "offiziell"

# ---------------------------------------------------------------------------
# Die Aufzählungstabellen — am 2026-08-17 aus /reference abgelesen
# ---------------------------------------------------------------------------
#
# Vollständig: /reference kennt zu diesen drei Feldern 13, 7 bzw. 3 Werte, und
# alle stehen hier. Was nicht abgebildet ist, wird zu None und damit zu „keine
# Angabe" — nie zu einem geratenen Nachbarwert.

#: `RISK_CLASS_ID` -> das Vokabular, das dieses Projekt überall verwendet.
#:
#: Die vier Altwerte (AIMDD, IVD-Anhang-II, IVD allgemein) haben in EUDAMEDs
#: heutigem `refdata.risk-class.*` keine Entsprechung; sie stammen aus den
#: Vorgängerrichtlinien. Sie bleiben unabgebildet — ein Gerät aus dieser Zeit
#: bekommt keine Risikoklasse angedichtet.
RISIKOKLASSEN: dict[float, str] = {
    -203.0: "refdata.risk-class.class-i",
    -204.0: "refdata.risk-class.class-iia",
    -205.0: "refdata.risk-class.class-iib",
    -10.0: "refdata.risk-class.class-iii",
    -199.0: "refdata.risk-class.class-a",
    -200.0: "refdata.risk-class.class-b",
    -201.0: "refdata.risk-class.class-c",
    -202.0: "refdata.risk-class.class-d",
    # Der einzige Altwert mit belegtem Bezeichner: /reference lässt ihn in der
    # ungarischen Zeile unübersetzt durch.
    -219.0: "refdata.risk-class.ivd-devices-self-testing",
}

#: `APPLICABLE_LEGISLATION_ID` -> `refdata.applicable-legislation.*`.
#: `-3020` („None") und `-3021` („Unknown") sind ausdrücklich keine Angabe.
RECHTSRAHMEN: dict[float, str] = {
    -197.0: "refdata.applicable-legislation.mdr",
    -198.0: "refdata.applicable-legislation.ivdr",
    -53.0: "refdata.applicable-legislation.mdd",
    -54.0: "refdata.applicable-legislation.aimdd",
    -55.0: "refdata.applicable-legislation.ivdd",
}

#: `DEVICE_STATUS_TYPE_ID` -> `refdata.device-model-status.*`.
#: Als einziges Feld liefert `/reference` hier die rohen Bezeichner mit; die
#: Tabelle ist damit nicht abgeschrieben, sondern abgelesen.
MARKTSTATUS: dict[float, str] = {
    -11.0: "refdata.device-model-status.on-the-market",
    -12.0: "refdata.device-model-status.no-longer-on-the-market",
    -790.0: "refdata.device-model-status.not-intended-for-eu-market",
}

#: `SPECIAL_DEVICE_TYPE_ID` -> `refdata.special-device-type.*`. Nur die beiden
#: Softwarewerte sind für dieses Werkzeug erheblich; Brillen und Kontaktlinsen
#: bleiben unabgebildet.
SONDERTYPEN: dict[float, str] = {
    -47.0: "refdata.special-device-type.software",
    -43.0: "refdata.special-device-type.software",
}


#: Die Umkehrung, für die FILTERrichtung: `class-iii` -> -10.0.
#:
#: Gebraucht seit dem 2026-08-17: Die offizielle Schnittstelle filtert sehr wohl
#: nach `RISK_CLASS_ID` und `APPLICABLE_LEGISLATION_ID` — nur mit ihren eigenen
#: Kennzahlen, nicht mit dem refdata-Vokabular. `OFFIZIELLE_API.md` §3 hatte
#: beide als „mit HTTP 400 abgelehnt" geführt; die Ablehnung galt dem WERT
#: (`class-iii`), nicht dem Parameter. Gemessen an Brainlab:
#:
#:     MF_SRN allein                    406
#:     + RISK_CLASS_ID=-10.0              4   (Klasse III)
#:     + RISK_CLASS_ID=-203.0           235   (Klasse I)
#:     + APPLICABLE_LEGISLATION_ID=-197 330   (MDR)
#:     + APPLICABLE_LEGISLATION_ID=-53   76   (MDD)  -> 330+76 = 406, exakt
def _umkehr(tabelle: dict[float, str]) -> dict[str, float]:
    """Kurzform -> Kennzahl. `refdata.risk-class.class-iii` und `class-iii`
    führen beide zum Ziel, weil `ParsedQuery` die kurze Form trägt."""
    aus: dict[str, float] = {}
    for kennzahl, code in tabelle.items():
        aus[code] = kennzahl
        aus[code.rsplit(".", 1)[-1]] = kennzahl
    return aus


RISIKOKLASSEN_ID = _umkehr(RISIKOKLASSEN)
RECHTSRAHMEN_ID = _umkehr(RECHTSRAHMEN)


# ---------------------------------------------------------------------------
# Werkzeuge
# ---------------------------------------------------------------------------


def _zahl(wert: Any) -> float | None:
    """Der Datensatz liefert alles Numerische als Fließkommazahl."""
    try:
        return float(wert) if wert is not None else None
    except (TypeError, ValueError):
        return None


def _ganz(wert: Any) -> int | None:
    """`VERSION_NUMBER = 1.0` ist eine Eins, keine Kommazahl."""
    zahl = _zahl(wert)
    return int(zahl) if zahl is not None else None


def _flagge(wert: Any) -> bool | None:
    """0.0/1.0/null -> False/True/None.

    None bleibt None: „nichts eingetragen" ist etwas anderes als „nein", und
    dieser Unterschied trägt im ganzen Projekt (siehe `db/schema.sql`, Absatz
    zu den `*_fetched_at`-Spalten).
    """
    zahl = _zahl(wert)
    return None if zahl is None else bool(zahl)


def _text(wert: Any) -> str | None:
    """Leerstring und Leerzeichen zählen als nicht gesetzt."""
    if wert is None:
        return None
    gestutzt = str(wert).strip()
    return gestutzt or None


def _mehrsprachig(wert: Any) -> Any:
    """`'{"texts": [...]}'` als String -> das Objekt, das `store._text` erwartet.

    Kommt so aus `MF_ACTOR_NAMES` und `ACTOR_ABBREVIATED_NAMES`: JSON, aber in
    eine Zeichenkette eingepackt. Wer das durchreicht, speichert die geschweifte
    Klammer als Herstellernamen.
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


#: Schon gemeldete unabgebildete Kennzahlen — gegen Protokoll-Lärm.
_GEMELDET: set[float] = set()


def _refdata(tabelle: dict[float, str], wert: Any) -> dict[str, str] | None:
    """Kennzahl -> `{"code": "refdata…"}`, die Form, die `store._code()` liest.

    Ein unbekannter Wert wird zu None und **nicht** zum nächstbesten Eintrag.
    Eine falsche Risikoklasse wäre in einer Zulassungsrecherche schlimmer als
    eine fehlende.
    """
    zahl = _zahl(wert)
    if zahl is None:
        return None
    code = tabelle.get(zahl)
    if code is None:
        # Einmal je Kennzahl, nicht einmal je Datensatz: Bei 406 Geräten mit
        # demselben unabgebildeten Sondertyp stünden sonst 406 gleiche Zeilen
        # im Protokoll und verdeckten alles andere.
        if zahl not in _GEMELDET:
            _GEMELDET.add(zahl)
            log.info("Referenzkennzahl %s ist nicht abgebildet — bleibt leer "
                     "(siehe die Tabellen oben; unbekannt heißt hier "
                     "„keine Angabe“, nie „nächstbester Wert“)", zahl)
        return None
    return {"code": code}


# ---------------------------------------------------------------------------
# Die Umformungen
# ---------------------------------------------------------------------------


def als_suchtreffer(satz: dict[str, Any]) -> dict[str, Any]:
    """Offizieller Datensatz -> die D1-Form von `/devices/udiDiData`.

    Deckt jedes Feld ab, das `store.upsert_devices_from_search` liest. Zwei
    Abweichungen gegenüber einem echten D1-Treffer, beide unschädlich:

      * `ulid` heißt dort `UDI_DI_DATA_ULID`,
      * `basicUdiDiDataUlid` heißt `BASIC_UDI_DATA_ULID`.

    Was der offizielle Datensatz nicht kennt (`versionState`), bleibt leer und
    wird vom `COALESCE` der Upserts nicht überschrieben.
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
        # `versionState` gibt es offiziell nicht — `STATUS_ID` ist der
        # Aktiv-Zustand des Datensatzes und etwas anderes.
        "versionState": None,
        "latestVersion": _flagge(satz.get("LATEST_VERSION")),
        "versionNumber": _ganz(satz.get("VERSION_NUMBER")),
    }


#: `ACTOR_TYPE` (Klartext) -> der refdata-Code, den `db/store.py` erwartet.
#:
#: Die offizielle Schnittstelle liefert die Rolle ausgeschrieben („Manufacturer"),
#: die UI-Schnittstelle als Code. Gespeichert wird der Code, weil der Gerätefilter
#: `srn=` gegen ihn prüft (`db/store.AKTEUR_HERSTELLER`).
#:
#: Der Abzug führt drei weitere Rollen, die hier bewusst FEHLEN: „Notified
#: Body", „Competent Authority" und „European Commission" (gemessen am
#: 2026-08-19, zusammen drei von zweitausend Sätzen). Ihre refdata-Codes stehen
#: in keiner Quelle, die dieses Projekt gemessen hat — sie zu erfinden hieße,
#: eine Zuordnung zu behaupten. Sie landen ohne Code in der Tabelle, ihr
#: `role_name` steht im Klartext daneben, und die Herstellersuche betrachtet
#: sie ohnehin nicht. Wer sie eines Tages braucht, misst die Codes nach.
AKTEURSROLLEN: dict[str, str] = {
    "manufacturer": "refdata.actor-type.manufacturer",
    "authorised representative": "refdata.actor-type.authorised-representative",
    "importer": "refdata.actor-type.importer",
    "system or procedure pack producer":
        "refdata.actor-type.system-procedure-pack-producer",
    "system/procedure pack producer":
        "refdata.actor-type.system-procedure-pack-producer",
}

#: Felder des offiziellen Akteursabzugs, die NICHT gespeichert werden.
#:
#: `PRRC_FIRST_NAME` und `PRRC_FAMILY_NAME` benennen die für die Einhaltung der
#: Regulierungsvorschriften verantwortliche Person — einen Menschen mit Vor- und
#: Nachnamen. Alles andere im Abzug beschreibt ein Unternehmen; dies nicht.
#:
#: Das Werkzeug beantwortet keine Frage, für die dieser Name gebraucht würde,
#: und die Datenschutzerklärung sagt Datenminimierung zu. Also wird er beim
#: Einlesen verworfen und nicht etwa gespeichert und später ausgeblendet: Was
#: nicht in der Datenbank steht, kann auch nicht aus einer Sicherung fallen.
NICHT_UEBERNOMMEN = frozenset({"PRRC_FIRST_NAME", "PRRC_FAMILY_NAME"})


def als_akteur(satz: dict[str, Any]) -> dict[str, Any]:
    """Offizieller Akteursdatensatz -> die A1-Form der UI-Schnittstelle.

    Gegenrichtung zu `als_suchtreffer`, für den Akteursbestand. Der offizielle
    Abzug ist der **reichere** von beiden: Er trägt `STATUS` (aktiv/inaktiv),
    `STATUS_FROM_DATE` und `VERSION` mit, und dazu Webseite und
    Umsatzsteuernummer, die über die UI-Schnittstelle nur der Detailabruf je
    Akteur liefert — eine Anfrage pro Firma statt tausend Firmen pro Anfrage.

    Warum der Status zählt: Ohne ihn sind sieben Elekta-Registrierungen sieben
    gleichwertige Firmen. Gemessen am 2026-08-19 ist genau **eine** davon aktiv
    (SE-MF-000002125); die übrigen sechs sind abgemeldet. Sie ohne diesen
    Unterschied als Schwestergesellschaften anzubieten hieße, zu sechs
    stillgelegten Registrierungen einzuladen.

    `PRRC_*` wird verworfen, siehe `NICHT_UEBERNOMMEN`.
    """
    rolle = _text(satz.get("ACTOR_TYPE")) or ""
    return {
        "eudamedIdentifier": _text(satz.get("ACTOR_ID")),
        # Die offizielle Schnittstelle führt keine UUID mit. Sie bleibt leer und
        # wird vom COALESCE des Upserts nicht überschrieben — eine über die
        # UI-Schnittstelle bereits bekannte UUID überlebt den Abzug.
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
        # Ein Registrierungsdatum liefert der Abzug nicht; `STATUS_FROM_DATE`
        # ist der Beginn des JETZIGEN Status und etwas anderes.
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
    """„Active" -> `refdata.actor-status.active`. Unbekanntes bleibt roh."""
    kurz = (_text(wert) or "").strip().lower()
    return (f"refdata.actor-status.{kurz}"
            if kurz in ("active", "inactive") else kurz)


def _anschrift(satz: dict[str, Any]) -> str | None:
    """Die Anschrift aus ihren Bestandteilen — die UI-Form ist eine Zeile.

    Reihenfolge wie in der UI-Schnittstelle: Straße, Hausnummer, Postleitzahl,
    Ort. Fehlende Teile fallen weg, statt Kommas ohne Inhalt zu hinterlassen.
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
    """Offizieller Datensatz -> die D2-Form von `/devices/udiDiData/{uuid}`.

    Das ist der eigentliche Gewinn: Diese Felder kostet die UI-API **eine
    Anfrage je Gerät**, hier kommen sie im Suchergebnis mit.

    `cndNomenclatures` trägt genau einen Eintrag, weil der offizielle Datensatz
    genau einen `NOMENCLATURE_CODE` führt. Die Nomenklaturzuordnung ist aber
    mehrwertig: 19 von 1.183 Geräten im Bestand (1,6 %, gemessen 2026-08-17)
    haben zwei bis vier Codes. Der Upsert schreibt mit `ON CONFLICT DO NOTHING`
    in `device_emdn`, ergänzt also und verdrängt nichts — ein späterer
    UI-Detailabruf vervollständigt die Liste.

    `marketInfoLink` fehlt bewusst: Länderangaben gibt es offiziell nicht.
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
        # `SINGLE_USE` gibt es im offiziellen Datensatz nicht — weglassen statt
        # raten. Der COALESCE-Upsert lässt einen späteren UI-Wert zu.
        "cndNomenclatures": [{"code": code}] if code else [],
    }


def als_basic_udi(satz: dict[str, Any]) -> dict[str, Any]:
    """Offizieller Datensatz -> die D3-Form von `/devices/basicUdiData/…`.

    **Ohne Zertifikate** — die hat die offizielle Schnittstelle nicht. Was hier
    herüberkommt, sind die Produktmerkmale und der Rechtsrahmen, und genau die
    fehlen heute in `analysis_ready_devices` für die meisten Geräte.

    Wichtig für den Aufrufer: `store.upsert_basic_udi` stempelt
    `certificates_fetched_at` **immer**, auch bei leerer Zertifikatsliste — und
    das hieße hier fälschlich „abgefragt, nichts gefunden". Für die offizielle
    Quelle gehört deshalb `ingest` so gebaut, dass es die Felder direkt setzt
    statt über diesen Upsert zu gehen. Die Form steht hier trotzdem, weil sie
    die Feldzuordnung an einer Stelle festhält.
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
