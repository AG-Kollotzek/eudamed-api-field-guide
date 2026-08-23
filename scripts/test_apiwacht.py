"""Prüfstand für die API-Wacht — ohne Netz, ohne EUDAMED, ohne Kosten.

    python scripts/test_apiwacht.py

Geprüft wird der Teil, auf den es ankommt: **der Vergleich**. Die Aufnahme
selbst ist eine Reihe von HTTP-Aufrufen und braucht keinen Prüfstand; ob aus
zwei Aufnahmen die richtigen Schlüsse gezogen werden, dagegen sehr wohl. Ein
Wächter, der schweigt, wenn ein Filter ausfällt, ist schlimmer als keiner —
und dass er schweigt, merkt man erst, wenn es zu spät ist.

Die Fälle sind gebaute Wörterbücher, keine Aufzeichnungen: So lässt sich auch
prüfen, was hoffentlich nie vorkommt.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "watch"))

import apiwacht  # noqa: E402

GRUEN, ROT, GRAU, AUS = "\033[32m", "\033[31m", "\033[90m", "\033[0m"

_fehler = 0


def ok(bedingung: bool, text: str) -> None:
    global _fehler
    print(f"   {GRUEN + '✓' + AUS if bedingung else ROT + '✗' + AUS} {text}")
    if not bedingung:
        _fehler += 1


def gruppe(titel: str) -> None:
    print(f"\n{GRAU}— {titel} —{AUS}")


def aufnahme(**abweichung):
    """Eine vollständige, gesunde Aufnahme — Grundlage aller Fälle."""
    basis = {
        "format": apiwacht.FORMAT,
        "zeitpunkt": "2026-08-14T10:00:00+00:00",
        "build": "2.27.3",
        "basis": 1071,
        "gesamt": 3_000_000,
        "kanarienvogel": "fehler",
        "filter": {
            "riskClassCode": {"zustand": "wirkt", "zweck": "Risikoklasse",
                              "treffer": {"refdata.risk-class.class-iib": 500_000}},
            "applicableLegislation": {"zustand": "wirkt", "zweck": "Rechtsrahmen",
                                      "treffer": {"refdata.applicable-legislation.mdr": 2_000_000}},
        },
        "vokabular": {"refdata.risk-class.class-iib": 500_000,
                      "refdata.applicable-legislation.mdr": 2_000_000},
        "felder": {"geraetesuche": ["uuid", "tradeName", "primaryDi"],
                   "zertifikate": ["uuid", "certificateNumber"]},
        "seitengroesse": 300,
        "kontrolle": "verworfen",
        "schalter": {"ffDevice": True, "ffVigFsn": False, "ffSscpi": True},
        "wunschliste": {"countryIso2Code": {"zustand": "verworfen", "treffer": 1071},
                        "implantable": {"zustand": "verworfen", "treffer": 1071}},
        "praefixsuche": "ja",
        "praefixkette": {"Q01": 90_000, "Q0106": 71_630, "Q010601": 1071},
        "seitenzaehlung": "0-basiert",
        "fehler": [],
    }
    basis.update(abweichung)
    return basis


def offiziell(**abweichung):
    """Der Aufnahmeblock zur offiziellen Schnittstelle, im Normalzustand."""
    block = {
        "angenommen": {n: {"status": 200, "saetze": 5}
                       for n in apiwacht.OFFIZIELL_ANGENOMMEN},
        "abgelehnt": {n: {"status": 400} for n in apiwacht.OFFIZIELL_ABGELEHNT},
        "leerzeichen": {"ohne": 0, "mit": 1081},
        "cursor": True,
        "seitengroesse": 1000,
        "referenz": {f"{f}:{k}": True for f, k in apiwacht.REFERENZ_SCHLUESSEL},
    }
    block.update(abweichung)
    return block


def schweren(aenderungen):
    return [a.schwere for a in aenderungen]


def main() -> int:
    gruppe("Der Regelfall: nichts hat sich geändert")
    alt = aufnahme()
    neu = aufnahme(zeitpunkt="2026-08-28T10:00:00+00:00", basis=1120,
                   vokabular={"refdata.risk-class.class-iib": 520_000,
                              "refdata.applicable-legislation.mdr": 2_100_000})
    a = apiwacht.vergleiche(alt, neu)
    ok(a == [], "gewachsene Trefferzahlen sind KEINE Meldung "
                f"({len(a)} Änderungen)")

    gruppe("Der Fall, für den die Wacht gebaut ist")
    neu = aufnahme()
    neu["filter"]["riskClassCode"] = {
        "zustand": "ignoriert", "zweck": "Risikoklasse",
        "treffer": {"refdata.risk-class.class-iib": 3_000_000}}
    a = apiwacht.vergleiche(alt, neu)
    ok("kritisch" in schweren(a), "ausgefallener Filter -> kritisch")
    ok(any("wirkt nicht mehr" in x.text for x in a), "die Meldung benennt es")
    ok(any("ungefilterte" in x.folge for x in a),
       "und sagt, was das im Werkzeug bedeutet")

    gruppe("Verschwundene Codewerte — je nach Verhalten der API")
    # Gemessen am 2026-08-14: EUDAMED antwortet auf einen unbekannten Codewert
    # mit HTTP 400. Ein verschwundener Wert liefert dann KEINE Zahl. Die erste
    # Fassung dieser Datei hätte genau das übersprungen ("kein Messwert").
    neu = aufnahme(vokabular={"refdata.risk-class.class-iib": None,
                              "refdata.applicable-legislation.mdr": 2_000_000})
    ok("kritisch" in schweren(apiwacht.vergleiche(alt, neu)),
       "unbekannter Wert wird abgelehnt (HTTP 400) -> erkannt")

    # Dieselbe Lage, aber EUDAMED antwortet künftig mit 0 statt mit einem
    # Fehler. Der Kanarienvogel misst das bei jedem Lauf mit, die Prüfung
    # stellt sich um.
    alt_null = aufnahme(kanarienvogel="null")
    neu_null = aufnahme(kanarienvogel="null",
                        vokabular={"refdata.risk-class.class-iib": 0,
                                   "refdata.applicable-legislation.mdr": 2_000_000})
    ok("kritisch" in schweren(apiwacht.vergleiche(alt_null, neu_null)),
       "unbekannter Wert liefert 0 -> ebenfalls erkannt (Kanarienvogel)")

    # Und der dritte denkbare Fall: unbekannte Werte werden ignoriert, der
    # verschwundene Wert liefert also ALLES statt nichts.
    alt_alles = aufnahme(kanarienvogel="alles")
    neu_alles = aufnahme(kanarienvogel="alles",
                         vokabular={"refdata.risk-class.class-iib": 3_000_000,
                                    "refdata.applicable-legislation.mdr": 2_000_000})
    ok("kritisch" in schweren(apiwacht.vergleiche(alt_alles, neu_alles)),
       "unbekannter Wert liefert alles -> ebenfalls erkannt")
    ok(apiwacht.vergleiche(alt_alles, aufnahme(kanarienvogel="alles")) == [],
       "dieselbe Regel meldet den gesunden Fall nicht")
    ok(schweren(apiwacht.vergleiche(alt, aufnahme(kanarienvogel="null"))) == ["auffaellig"],
       "geändertes Verhalten bei unbekannten Werten -> auffällig, nicht kritisch")

    gruppe("Die Gegenrichtung: neue Möglichkeiten nicht verpassen")
    neu = aufnahme(wunschliste={
        "countryIso2Code": {"zustand": "wirkt", "treffer": 300},
        "implantable": {"zustand": "verworfen", "treffer": 1071}})
    a = apiwacht.vergleiche(alt, neu)
    ok(schweren(a) == ["auffaellig"], "neu wirkender Filter -> auffällig, kein Alarm")
    ok(any("Land" in x.folge for x in a),
       "die Meldung sagt, was damit möglich würde")
    ok(any("gegenprüfen" in x.folge for x in a),
       "und mahnt die inhaltliche Gegenprobe an")

    gruppe("Die Grundannahme unter allen „wirkt nicht“")
    a = apiwacht.vergleiche(alt, aufnahme(kontrolle="abgelehnt"))
    ok(schweren(a) == ["auffaellig"],
       "unbekannte Parameter werden nicht mehr verworfen -> gemeldet")
    ok(any("FILTER_MATRIX" in x.folge for x in a),
       "mit dem Hinweis, dass die Filtermatrix neu zu bewerten wäre")

    gruppe("Präfixsuche und Seitenzählung (aus Probe 03 und 02)")
    a = apiwacht.vergleiche(alt, aufnahme(praefixsuche="nein"))
    ok(schweren(a) == ["kritisch"], "cndCode ohne Präfixsuche -> kritisch")
    ok(any("Gruppensuche" in x.folge for x in a), "und benennt die Folge")
    a = apiwacht.vergleiche(alt, aufnahme(seitenzaehlung="auffaellig"))
    ok(schweren(a) == ["kritisch"], "Seitenzählung nicht mehr 0-basiert -> kritisch")

    gruppe("Schalter der öffentlichen Seite")
    # Der Fall, für den die Prüfung gebaut ist: EUDAMED gibt die
    # Sicherheitsmeldungen frei. Das ist keine Störung, sondern eine neue
    # Möglichkeit — und damit „auffällig", nicht „kritisch".
    a = apiwacht.vergleiche(alt, aufnahme(schalter={
        "ffDevice": True, "ffVigFsn": True, "ffSscpi": True}))
    ok(schweren(a) == ["auffaellig"], "freigeschalteter Schalter -> auffällig")
    ok(any("gotchas" in x.folge for x in a),
       "…mit dem Verweis auf den Befund, der davon abhängt")

    a = apiwacht.vergleiche(alt, aufnahme(schalter={
        "ffDevice": False, "ffVigFsn": False, "ffSscpi": True}))
    ok(any("Grundlage der UI-API" in x.folge for x in a),
       "abgeschalteter Gerätebestand -> die Folge steht dabei")

    a = apiwacht.vergleiche(alt, aufnahme(schalter={
        "ffDevice": True, "ffVigFsn": False, "ffSscpi": True, "ffNeu": True}))
    ok(schweren(a) == ["hinweis"], "ein neuer Schalter -> Hinweis, kein Alarm")
    ok(apiwacht.vergleiche(alt, aufnahme()) == [],
       "unveränderte Schalter melden nichts")
    ok(not apiwacht.vollstaendig(aufnahme(schalter={}))[0],
       "ohne Schalter: Aufnahme unbrauchbar")

    gruppe("Feldnamen")
    neu = aufnahme(felder={"geraetesuche": ["uuid", "primaryDi"],
                           "zertifikate": ["uuid", "certificateNumber"]})
    a = apiwacht.vergleiche(alt, neu)
    ok("kritisch" in schweren(a), "verschwundenes Feld -> kritisch")
    neu = aufnahme(felder={"geraetesuche": ["uuid", "tradeName", "primaryDi", "neu"],
                           "zertifikate": ["uuid", "certificateNumber"]})
    a = apiwacht.vergleiche(alt, neu)
    ok(schweren(a) == ["hinweis"], "neues Feld -> Hinweis, kein Alarm")

    gruppe("Mengen und Datenstand")
    ok(schweren(apiwacht.vergleiche(alt, aufnahme(basis=800))) == ["auffaellig"],
       "Rückgang um 25 % -> auffällig")
    ok(apiwacht.vergleiche(alt, aufnahme(basis=1000)) == [],
       "Rückgang um 7 % -> unter der Schwelle, keine Meldung")
    ok(schweren(apiwacht.vergleiche(alt, aufnahme(build="2.28.0"))) == ["auffaellig"],
       "neue buildVersion -> auffällig, nicht kritisch")
    ok(schweren(apiwacht.vergleiche(alt, aufnahme(seitengroesse=100))) == ["auffaellig"],
       "kleinere Seitengröße -> auffällig")

    gruppe("Unvollständige Aufnahmen (der DNS-Ausfall vom 2026-08-14)")
    ok(apiwacht.vollstaendig(aufnahme())[0], "gesunde Aufnahme ist brauchbar")
    ok(not apiwacht.vollstaendig(aufnahme(gesamt=None))[0],
       "ohne Gesamtbestand: unbrauchbar")
    kaputt = aufnahme()
    kaputt["filter"]["riskClassCode"]["zustand"] = "unbekannt"
    brauchbar, mangel = apiwacht.vollstaendig(kaputt)
    ok(not brauchbar and "riskClassCode" in mangel,
       f"ein ungemessener Filter macht die Aufnahme unbrauchbar ({mangel})")
    ok(not apiwacht.vollstaendig(aufnahme(kanarienvogel=None))[0],
       "ohne Kanarienvogel: unbrauchbar, weil Codewerte nicht deutbar wären")
    ok(not apiwacht.vollstaendig(aufnahme(felder={}))[0],
       "ohne Feldnamen: unbrauchbar")
    ok(not apiwacht.vollstaendig(aufnahme(schalter={}))[0],
       "ohne die Schalter der öffentlichen Seite: unbrauchbar")


    gruppe("Die offizielle Schnittstelle")
    basis = aufnahme(offiziell=offiziell())

    # 1. Ein tragender Parameter fällt aus.
    kaputt = aufnahme(offiziell=offiziell(
        angenommen={**{n: {"status": 200, "saetze": 5}
                       for n in apiwacht.OFFIZIELL_ANGENOMMEN},
                    "MF_SRN": {"status": 500, "saetze": 0}}))
    a = apiwacht.vergleiche(basis, kaputt)
    ok("kritisch" in schweren(a), "ausgefallener MF_SRN -> kritisch")
    ok(any("capabilities" in x.folge for x in a),
       "und sagt, wo es angepasst gehört")

    # 2. Ein abgelehnter Parameter geht plötzlich — eine neue Fähigkeit.
    #    `IMPLANTABLE` steht dafür, seit `RISK_CLASS_ID` am 2026-08-17 von
    #    genau dieser Prüfung als angenommen entlarvt wurde und in die andere
    #    Liste gewandert ist.
    besser = aufnahme(offiziell=offiziell(
        abgelehnt={**{n: {"status": 400} for n in apiwacht.OFFIZIELL_ABGELEHNT},
                   "IMPLANTABLE": {"status": 200}}))
    a = apiwacht.vergleiche(basis, besser)
    ok(any("neue Fähigkeit" in x.text for x in a),
       "400 wird 200 -> als Fähigkeit gemeldet, nicht als Störung")
    ok(any("Fall C" in x.folge for x in a),
       "und nennt die Folge: das lokale Nachfiltern entfiele")

    # 3. Der Leerzeichen-Fehler ist behoben — die Kapselung muss weg.
    geheilt = aufnahme(offiziell=offiziell(
        leerzeichen={"ohne": 1081, "mit": 1081}))
    a = apiwacht.vergleiche(basis, geheilt)
    ok("kritisch" in schweren(a), "behobener Leerzeichen-Fehler -> kritisch")
    ok(any("entfernt werden" in x.folge for x in a),
       "denn die Kapselung sucht sonst ins Leere")

    # 4. Ein Referenzschlüssel verschwindet.
    ohne_ref = aufnahme(offiziell=offiziell(
        referenz={f"{f}:{k}": (f != "RISK_CLASS_ID")
                  for f, k in apiwacht.REFERENZ_SCHLUESSEL}))
    a = apiwacht.vergleiche(basis, ohne_ref)
    ok(any("normalisierung" in x.folge for x in a),
       "fehlender Referenzschlüssel zeigt auf die Zuordnungstabelle")

    # 5. Der Normalfall bleibt still.
    a = apiwacht.vergleiche(basis, aufnahme(offiziell=offiziell()))
    ok(a == [], f"unveränderte offizielle Schnittstelle -> keine Meldung "
                f"({len(a)} Änderungen)")

    gruppe("Formatwechsel")
    a = apiwacht.vergleiche(aufnahme(format=0), aufnahme())
    ok(len(a) == 1 and a[0].schwere == "hinweis",
       "anderes Aufnahmeformat -> ein Hinweis statt lauter Falschmeldungen")

    print(f"\n{GRUEN + 'Alle Prüfungen bestanden.' + AUS if not _fehler else ROT + f'{_fehler} ABWEICHUNG(EN).' + AUS}")
    return 1 if _fehler else 0


if __name__ == "__main__":
    raise SystemExit(main())
