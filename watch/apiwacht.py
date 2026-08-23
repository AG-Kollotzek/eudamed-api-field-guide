"""Beobachtet die EUDAMED-Schnittstelle über die Zeit — regelmäßig, unbeaufsichtigt.

    python watch/apiwacht.py            # nur laufen lassen, wenn fällig
    python watch/apiwacht.py --jetzt    # sofort, unabhängig vom Abstand

Eine Anwendung kann die Wacht auch beim eigenen Start nebenherlaufen lassen
(siehe `starte_im_hintergrund`).

## Warum das nötig ist

EUDAMED wird gerade erst verpflichtend und ist im Aufbau. Die Schnittstelle, auf
der dieses Werkzeug steht, ist **inoffiziell**: keine Versionszusage, kein
Änderungsprotokoll, keine Ankündigung. Und die gefährlichste Eigenschaft der API
ist längst gemessen (docs/filter-matrix.md):

> **EUDAMED verwirft unbekannte Parameter stillschweigend.** HTTP 200, plausible
> Trefferzahl, kein Hinweis — nur eben ungefiltert.

Wird `riskClassCode` morgen umbenannt, liefert dieses Werkzeug weiterhin
Ergebnisse. Sie sind dann nur nicht mehr auf die gefragte Risikoklasse
eingeschränkt, und niemand sieht es. Genau dieser Fall ist der Grund für diese
Datei: Er ist **nicht** an einer Fehlermeldung zu erkennen, sondern nur daran,
dass sich eine Trefferzahl nicht mehr verändert, wenn sie sich verändern müsste.

## Was gemessen wird — und warum genau das

Nicht „alles", sondern die Dinge, deren stille Änderung das Werkzeug falsch
antworten ließe:

1. **Wirken die Filter noch?** Je Parameter wird **jeder zulässige Wert einzeln**
   gezählt. Unterscheiden sich die Zahlen deutlich, filtert der Parameter; sind
   sie alle gleich, wird er verworfen. Das ist die einzige Prüfung, die eine
   stille Verwerfung überhaupt sichtbar macht — und sie braucht weder einen
   stabilen Bezugspunkt noch eine passend gewählte Referenzgruppe (siehe
   `FILTER`, dort steht, woran der erste Entwurf gescheitert ist).
2. **Gibt es die Codewerte noch?** Dieselben Zahlen beantworten das gleich mit:
   `refdata.risk-class.class-iib` und die übrigen Aufzählungswerte stehen fest
   im Planner. Woran ein verschwundener Wert zu erkennen ist, hängt davon ab,
   was EUDAMED mit unbekannten Werten macht — das misst der `KANARIENVOGEL` bei
   jedem Lauf mit, statt es anzunehmen.
3. **Heißen die Felder noch so?** Aus `tradeName`, `primaryDi` oder
   `deviceCertificateInfoList` liest der Import. Ein umbenanntes Feld heißt:
   Spalte leer, nicht Fehler.
4. **Welcher Datenstand?** `buildVersion` ist der billigste Hinweis darauf, dass
   überhaupt etwas passiert ist.

Dazu drei Prüfungen, die aus den Probes hierher gewandert sind, weil sie Dinge
betreffen, die sich ändern können — während eine Probe eine einmal beantwortete
Frage bleibt:

5. **Trägt `cndCode` noch die Präfixsuche?** (aus Probe 03) Die gesamte
   Gruppensuche beruht darauf, dass ein Elterncode seine Unterknoten miterfasst.
6. **Ist die Seitenzählung noch 0-basiert?** (aus Probe 02) Sonst wäre jede
   mehrseitige Trefferliste um eine Seite versetzt.
7. **Gibt es Filter, die es vorher nicht gab?** (aus Probe 05) Die
   `WUNSCHLISTE` prüft Parameter, die heute wirkungslos sind und deren
   Auftauchen dem Werkzeug eine neue Auskunft ermöglichen würde. Dazu die
   `KONTROLLE`: Sie belegt, dass „Trefferzahl unverändert“ überhaupt noch
   „wird ignoriert“ heißt.

Rund 34 Anfragen je Aufnahme, mit 2 s Pause dazwischen — gut zwei Minuten, alle
14 Tage.

## Was diese Wacht NICHT leistet

Ehrlich benannt, weil ein Wächter, dem man mehr zutraut als er kann, schlimmer
ist als keiner:

* **Bedeutungsänderungen bei plausiblen Zahlen** sind unsichtbar. Wenn
  `riskClassCode` künftig „mindestens diese Klasse" statt „genau diese Klasse"
  bedeutet, unterscheiden sich die Werte weiterhin, und die Wacht schweigt.
* **Neue Möglichkeiten** findet sie nur, soweit jemand sie vorher aufgeschrieben
  hat. Die `WUNSCHLISTE` prüft acht Parameter, von denen wir uns etwas
  versprechen — ein neunter, an den niemand gedacht hat, bleibt unentdeckt.
  Dafür sind weiterhin die Probes da.
* **Zwischen zwei Aufrufen passiert nichts.** Es gibt keinen Dienst, der im
  Hintergrund läuft — bewusst nicht, siehe unten. Läuft die Wacht acht Wochen
  nicht, ist die letzte Aufnahme acht Wochen alt. Das Protokoll schreibt
  deshalb den **tatsächlichen** Abstand mit, nie den geplanten.

## Warum kein Dienst und kein Cron

Ein Hintergrunddienst, der ohne Zutun eine fremde, inoffizielle API abfragt, ist
etwas anderes als ein Werkzeug, das beim Aufruf einmal nachsieht. Das zweite ist
gegenüber einer öffentlich finanzierten Infrastruktur vertretbar, das erste
richtet dieses Repo bewusst nicht ein. Der Preis ist die Lücke oben, und die ist
verkraftbar: Wer die Wacht nicht laufen lässt, wird von einer Änderung auch
nicht überrascht.

## Warum Zahlen kein Alarm sind

EUDAMED wächst täglich. Ein Vergleich, der jede geänderte Trefferzahl meldet,
meldet bei jedem Lauf etwas — und ein Protokoll, das immer etwas meldet, liest
nach dem dritten Mal niemand mehr. Deshalb sind **Struktur** und **Menge**
getrennt: Feldnamen, Filterwirkung und Codewerte sind Befunde, Trefferzahlen
sind Beiwerk. Eine Zahl wird nur auffällig, wenn sie **fällt** (`RUECKGANG`) —
Wachstum ist der Normalfall, Schrumpfen ist es nicht.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

#: Die Repo-Wurzel — dieses Modul liegt in watch/, eine Ebene darunter.
WURZEL = Path(__file__).resolve().parent.parent

#: Die Aufnahmen selbst — Rohdaten, maschinenlesbar, nicht im Repo (gitignored).
AUFNAHMEN = WURZEL / "output" / "apiwacht"

#: Das Protokoll — im Repo, weil es die Geschichte der Schnittstelle ist: Wann
#: hat sie sich wie verändert? Ein versioniertes Quelldokument.
PROTOKOLL = WURZEL / "docs" / "changelog.md"

#: Vorgesehener Abstand zwischen zwei Aufnahmen.
ABSTAND_TAGE = 14

#: Pause zwischen zwei Anfragen. Länger als im Import (1,0 s): Diese Aufnahme hat
#: es nicht eilig und läuft womöglich neben einer echten Recherche her. Wer
#: nebenher misst, hat hinten anzustehen.
PAUSE_S = 2.0

#: Erst so viele Sekunden nach dem Start beginnen. Die erste Anfrage einer
#: Testperson soll nicht mit der Wacht um die Leitung konkurrieren.
VERZOEGERUNG_S = 15.0

#: Aufbau der Aufnahme. Ändert sich das Format, ist ein Vergleich mit älteren
#: Aufnahmen nicht mehr aussagekräftig — dann wird die nächste Aufnahme wieder
#: zur Erstaufnahme statt zu einem Vergleich mit falschem Ergebnis.
#:
#: 1 (2026-08-14)  Filter, Codewerte, Feldnamen, Seitengröße
#: 2 (2026-08-14)  dazu: Wunschliste, Kontrollprobe, Präfixsuche, Seitenzählung
#: 3 (2026-08-16)  dazu: die Schalter der öffentlichen Seite
#: 4 (2026-08-17)  dazu: die offizielle Schnittstelle — beide Zugänge, auf die
#:                 das Werkzeug seit dem Zusatzpfad baut
FORMAT = 4

#: Referenzgruppe für alle Messungen: groß genug für aussagekräftige Zahlen,
#: klein genug, dass eine Zählabfrage schnell ist. Dieselbe Gruppe wie in
#: Probe 05, damit die Werte dort vergleichbar bleiben.
REFERENZ_CND = "Q010601"

#: Die Filter, auf denen das Werkzeug steht — mit allen Werten, die der Planner
#: setzen kann. Je Parameter wird jeder Wert einzeln gezählt. Daraus folgt beides
#: auf einmal: ob der Parameter überhaupt filtert (die Zahlen unterscheiden sich)
#: und ob es jeden Wert noch gibt.
#:
#: Der erste Entwurf verglich stattdessen eine Referenzgruppe mit und ohne
#: Filter. Das war aus zwei gemessenen Gründen untauglich (2026-08-14):
#:
#:   * Zählstände schwanken. Dieselbe Gruppe lieferte 1071 und Sekunden später
#:     1079 Treffer — der „gefilterte" Wert lag ÜBER dem ungefilterten, und der
#:     Filter galt als ausgefallen. EUDAMED ist ein lebender Bestand.
#:   * Die Referenzgruppe war zufällig einfarbig. In Q010601 (Dentallegierungen)
#:     ist fast alles Klasse IIa; ein Filter auf IIa nimmt dort nichts weg, ganz
#:     gleich wie gut er funktioniert.
#:
#: Der Vergleich mehrerer Werte untereinander hat beide Probleme nicht: Er
#: braucht keinen stabilen Bezugspunkt und keine passend gewählte Gruppe. Vier
#: Risikoklassen, die 1,5 Mio · 843 Tsd · 496 Tsd · 146 Tsd liefern, beweisen die
#: Filterwirkung ohne jede weitere Annahme.
FILTER: dict[str, dict[str, Any]] = {
    "riskClassCode": {
        "zweck": "Risikoklasse",
        "werte": ("refdata.risk-class.class-i", "refdata.risk-class.class-iia",
                  "refdata.risk-class.class-iib", "refdata.risk-class.class-iii"),
    },
    "applicableLegislation": {
        "zweck": "Rechtsrahmen",
        "werte": ("refdata.applicable-legislation.mdr",
                  "refdata.applicable-legislation.mdd",
                  "refdata.applicable-legislation.ivdr"),
    },
    "deviceStatusCode": {
        "zweck": "Marktstatus",
        "werte": ("refdata.device-model-status.on-the-market",
                  "refdata.device-model-status.no-longer-on-the-market",
                  "refdata.device-model-status.not-intended-for-eu-market"),
    },
}

#: Zählstände schwanken zwischen zwei Abrufen; EUDAMED ist ein lebender Bestand.
#: Unterschiede unterhalb dieses Anteils am Gesamtbestand gelten deshalb als
#: „gleich". Gemessen wurde eine Schwankung von 0,7 % innerhalb einer Minute.
TOLERANZ = 0.02

#: Filter, die es **nicht** gibt — und deren Auftauchen etwas ändern würde.
#:
#: Alle 35 wirkungslosen Parameter aus ../FILTER_MATRIX.md jedes Mal zu prüfen
#: wäre Verschwendung; die meisten wären auch dann nur nett. Hier stehen die, bei
#: denen das Werkzeug heute „kann ich nicht" sagen muss und mit denen es morgen
#: eine neue Frage beantworten könnte. Je Eintrag: was daraus würde.
#:
#: Diese Liste ist die Antwort auf eine Lücke, die die Wacht sonst hätte: Sie
#: prüft, was wir nutzen. Was EUDAMED **dazubekommt**, sähe sie nie — und ein
#: Register im Aufbau bekommt Dinge dazu.
WUNSCHLISTE: dict[str, dict[str, str]] = {
    "countryIso2Code": {
        "wert": "DE",
        "nutzen": "Verfügbarkeit nach Land als Suchfilter statt als Auswertung "
                  "nach einem Detailabruf je Gerät — die mit Abstand teuerste "
                  "Auskunft des Werkzeugs würde billig.",
    },
    "notifiedBodySrn": {
        "wert": "0197",
        "nutzen": "Benannte Stelle auf Geräteebene. Heute nur herstellergenau "
                  "über das Zertifikatsmodul, und damit nie ganz belastbar.",
    },
    "implantable": {
        "wert": "true",
        "nutzen": "Implantierbarkeit als Filter. Heute wird sie über die "
                  "EMDN-Kategorien J und P angenähert — eine Näherung, die "
                  "Zahnimplantate nachweislich verfehlt.",
    },
    "sterile": {
        "wert": "true",
        "nutzen": "Sterile Lieferung als Filter statt als Angabe im Detail.",
    },
    "singleUse": {
        "wert": "true",
        "nutzen": "Einmal- gegen Mehrfachgebrauch als Filter.",
    },
    "expiryDateFrom": {
        "wert": "2027-01-01",
        "nutzen": "Ablaufdatum serverseitig eingrenzen. Heute muss dafür die "
                  "Zertifikatslage jedes einzelnen Geräts geladen werden — bei "
                  "380 Geräten rund 20 Minuten.",
    },
    "versionDateFrom": {
        "wert": "2026-01-01",
        "nutzen": "Neu registrierte Produkte seit einem Stichtag — heute "
                  "unbeantwortbar, weil kein Datumsfilter wirkt.",
    },
    "manufacturerName": {
        "wert": "Braun",
        "nutzen": "Herstellername trennscharf statt über den unscharfen "
                  "Freitextparameter, der auch Produktnamen trifft.",
    },
}

#: Ein Parametername, den es sicher nicht gibt. Er belegt die Grundannahme, auf
#: der jedes „wirkt nicht" beruht: EUDAMED verwirft unbekannte Parameter
#: stillschweigend. Wäre das eines Tages nicht mehr so — etwa weil unbekannte
#: Namen abgelehnt werden —, hieße „Trefferzahl unverändert" plötzlich etwas
#: anderes, und die halbe Wacht wäre stillschweigend wirkungslos.
#:
#: Dieselbe Kontrollprobe steht am Anfang von ../FILTER_MATRIX.md. Sie gehört
#: wiederholt, nicht einmal gemacht.
KONTROLLE = ("diesenParameterGibtEsNicht", "egal")

#: Schalter, mit denen EUDAMED steuert, was auf der öffentlichen Seite
#: überhaupt erscheint. Am 2026-08-16 über `/configurationParameters?scope=PUBLIC`
#: gefunden — die offizielle Oberfläche fragt sie beim Start selbst ab.
#:
#: Sie sind das genaue Gegenstück zur `WUNSCHLISTE`: Dort geht es um Filter, die
#: dazukommen könnten, hier um ganze Bestände. Zwei Schalter stehen heute auf
#: „aus" und beantworten damit eine Frage, die das Werkzeug seinen Nutzern
#: gegenüber bisher nur behaupten konnte:
#:
#:     ffVigFsn = false   Sicherheitsmeldungen (FSN) sind öffentlich NICHT
#:                        sichtbar. Der Satz „Rückrufe geben wir nicht her"
#:                        ist damit belegt und nicht geschätzt.
#:     ffSscpi  = true    Die Kurzberichte über Sicherheit und klinische
#:                        Leistung sind öffentlich — ein Bestand, den das
#:                        Werkzeug bisher nicht nutzt.
#:
#: Springt einer dieser Schalter um, ändert sich, was das Werkzeug beantworten
#: kann. Das gehört ins Protokoll, sobald es passiert — und nicht erst, wenn
#: jemand zufällig darüber stolpert.
#:
#: Die vollständige Tafel mit allen zwölf Werten, dazu die offizielle
#: öffentliche API, auf die einer der Schalter verweist, steht in
#: OFFIZIELLE_API.md.
SCHALTER = "/configurationParameters"

#: Was ein umgelegter Schalter für dieses Werkzeug bedeutet. Nur für die, bei
#: denen die Antwort feststeht — der Rest bekommt einen allgemeinen Satz.
SCHALTER_FOLGEN = {
    "ffVigFsn": "Sicherheitsmeldungen und Rückrufe (FSN) wären damit öffentlich "
                "sichtbar — bisher sind sie es nicht (siehe docs/gotchas.md, "
                "Punkt 9). Jede Auskunft, die sich darauf stützt, gehört dann "
                "geprüft.",
    "ffSscpi": "Die Kurzberichte über Sicherheit und klinische Leistung (SSCP) "
               "wären damit öffentlich. Sie sind die einzige klinische Quelle in "
               "EUDAMED überhaupt.",
    "ffDevice": "Ohne diesen Schalter zeigt die öffentliche Seite keine Geräte "
                "mehr — dann ist die Grundlage der UI-API weg.",
    "ffCertificate": "Betrifft die Zertifikatsanzeige, also die teuerste und "
                     "heikelste Auskunft des Werkzeugs.",
    "ffNomenclature": "Betrifft die EMDN-Nomenklatur, aus der die Vorauswahl der "
                      "Codes kommt.",
    "ffPublicDataApiFunction": "Betrifft die offizielle öffentliche Daten-API "
                               "(api.datalake.sante.service.ec.europa.eu) — die "
                               "dokumentierte Alternative zur inoffiziellen "
                               "Schnittstelle, auf der dieses Werkzeug läuft.",
}

#: Codes zunehmender Länge für die Präfixprobe. Erwartet wird eine Kette:
#: count(Q01) >= count(Q0106) >= count(Q010601), alle größer null.
PRAEFIXKETTE = ("Q01", "Q0106", "Q010601")

#: Ab diesem Rückgang gilt eine Trefferzahl als auffällig. Wachstum wird nie
#: gemeldet — EUDAMED füllt sich, das ist der erwartete Zustand.
RUECKGANG = 0.10

#: Ein Wert, den es sicher nicht gibt. Er beantwortet die Frage, die man sonst
#: raten müsste: **Was tut EUDAMED mit einem unbekannten Aufzählungswert?**
#:
#: Bei den Parameter*namen* ist es gemessen — sie werden stillschweigend
#: verworfen. Bei den *Werten* wären drei Antworten denkbar: null Treffer, alle
#: Treffer, oder eine Fehlermeldung. Davon hängt ab, woran ein verschwundener
#: Codewert überhaupt zu erkennen ist.
#:
#: Am 2026-08-14 gemessen: **HTTP 400.** Damit wäre die naheliegende Prüfung
#: („liefert der Wert noch Treffer?") wirkungslos gewesen — ein verschwundener
#: Wert liefert gar keine Antwort, und eine ausbleibende Antwort hätte die erste
#: Fassung dieser Datei stillschweigend übersprungen. Gemessen wird es trotzdem
#: bei jedem Lauf: Die Antwort von heute ist keine Zusage für morgen.
KANARIENVOGEL = ("riskClassCode", "refdata.risk-class.gibt-es-nicht")


# ---------------------------------------------------------------------------
# Die offizielle Schnittstelle — schärfer prüfbar als die UI-API
# ---------------------------------------------------------------------------
#
# Seit dem 2026-08-17 fährt das Werkzeug zwei Zugänge (client/capabilities.py).
# Der offizielle hat als einziger einen veröffentlichten Vertrag, und er lässt
# sich deshalb **direkt** prüfen statt über Trefferzahlvergleiche:
#
#     UI-API        unbekannter Parameter -> still verworfen
#                   => Wirkung nur aus dem Unterschied zweier Zählungen erschließbar
#     offizielle    unbekannter Parameter -> HTTP 400
#                   => die Zusage ist eine Ja/Nein-Frage
#
# Das macht die Prüfung hier belastbarer als alles, was oben steht — und die
# Gegenprobe (`OFFIZIELL_ABGELEHNT`) sogar zu einer Chance: Antwortet ein
# abgelehnter Parameter plötzlich mit 200, ist eine Fähigkeit dazugekommen.

#: Parameter, auf denen `client/official_client.py` steht. Müssen HTTP 200
#: liefern — fällt einer aus, bricht der Zusatzpfad.
OFFIZIELL_ANGENOMMEN: dict[str, str] = {
    "MF_SRN": "DE-MF-000006183",
    "PRIMARY_DI": "E4947662361",
    "NOMENCLATURE_CODE": " Q010601",
    # Am 2026-08-17 von dieser Wacht selbst gefunden: OFFIZIELLE_API.md führte
    # beide als „HTTP 400". Die Ablehnung galt dem WERT (`class-iii`), nicht
    # dem Parameter — mit der Kennzahl filtern sie serverseitig.
    "RISK_CLASS_ID": "-10.0",
    "APPLICABLE_LEGISLATION_ID": "-197.0",
}

#: Nachweislich abgelehnt (HTTP 400). Ein plötzliches 200 wäre keine Störung,
#: sondern eine neue Fähigkeit: `RISK_CLASS_ID` etwa würde Fall C überflüssig
#: machen und die Trefferliste serverseitig filterbar.
OFFIZIELL_ABGELEHNT: dict[str, str] = {
    "IMPLANTABLE": "true",
    "STERILE": "true",
    "MF_NAME": "Brainlab",
    "DEVICE_STATUS_TYPE_ID": "-11.0",
}

#: Der Leerzeichen-Fehler: Die gespeicherten Nomenklaturwerte tragen eines
#: (`" Q010601"`). `official_client.iter_udi` kapselt das. Verschwindet der
#: Fehler, MUSS die Kapselung weg — sonst sucht sie ins Leere und liefert
#: schweigend null Treffer.
LEERZEICHEN_PROBE = ("Q010601", " Q010601")

#: Die Aufzählungstabellen aus `client/normalisierung.py` sind am echten
#: Bestand belegt (1.880 Vergleiche, null Abweichungen — 2026-08-17). Geprüft
#: wird hier nur, ob `/reference` sie überhaupt noch führt: Verschwände ein
#: Schlüssel, stünde die Risikoklasse offiziell geholter Geräte auf NULL.
REFERENZ_SCHLUESSEL = (("RISK_CLASS_ID", -10.0),
                       ("APPLICABLE_LEGISLATION_ID", -197.0),
                       ("DEVICE_STATUS_TYPE_ID", -11.0))


# ---------------------------------------------------------------------------
# Aufnahme
# ---------------------------------------------------------------------------


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kurz(text: object, zeichen: int = 140) -> str:
    """Eine Fehlermeldung auf Protokolllänge. Ein Verbindungsfehler aus
    `requests` ist 400 Zeichen lang und sagt in den ersten 60 alles."""
    einzeilig = " ".join(str(text).split())
    return einzeilig if len(einzeilig) <= zeichen else einzeilig[:zeichen - 1] + "…"


def _felder(eintrag: Any, praefix: str = "", tiefe: int = 2) -> list[str]:
    """Die Feldnamen eines Datensatzes, verschachtelte mit Punkt.

    Zwei Ebenen genügen und sind Absicht: Der Import liest `deviceStatusType.code`
    und `tradeName.texts`, aber nichts darunter. Tiefer zu gehen hieße, jede
    Umsortierung in den Textblöcken als Änderung zu melden.
    """
    if not isinstance(eintrag, dict) or tiefe <= 0:
        return []
    namen: list[str] = []
    for schluessel, wert in eintrag.items():
        pfad = f"{praefix}{schluessel}"
        namen.append(pfad)
        if isinstance(wert, dict):
            namen += _felder(wert, f"{pfad}.", tiefe - 1)
        elif isinstance(wert, list) and wert and isinstance(wert[0], dict):
            namen += _felder(wert[0], f"{pfad}[].", tiefe - 1)
    return sorted(set(namen))


def vollstaendig(aufnahme: dict[str, Any]) -> tuple[bool, str]:
    """Taugt diese Aufnahme als Bezugspunkt für den nächsten Vergleich?

    Diese Prüfung ist am 2026-08-14 aus einem echten Fehlschlag entstanden: Die
    erste Aufnahme lief mitten in einen DNS-Ausfall. Vier von fünf Beständen
    waren nicht erreichbar, zwei der drei Filter blieben „unbekannt“ — und das
    Ergebnis wurde als **Erstaufnahme** abgelegt und im Protokoll als
    Ausgangszustand ausgewiesen.

    Der Schaden daran ist nicht die verlorene Messung, die ist wiederholbar.
    Der Schaden ist, dass die nächste Aufnahme gegen einen Bezugspunkt
    verglichen hätte, in dem die halbe Schnittstelle fehlt: Ein Filter, der
    nie gemessen wurde, kann nicht als ausgefallen auffallen. Ein Wächter mit
    einem falschen Bezugspunkt ist schlimmer als keiner, weil er Ruhe meldet.

    Verlangt wird deshalb das, was den Vergleich trägt: die Basiszahl, die
    Feldnamen der Gerätesuche, ein Ergebnis für **jeden** Filter und die beiden
    Zahlen, mit denen sich Codewerte überhaupt deuten lassen.
    """
    if not aufnahme.get("gesamt"):
        return False, "Gesamtbestand nicht messbar"
    if not (aufnahme.get("felder") or {}).get("geraetesuche"):
        return False, "keine Feldnamen aus der Gerätesuche"
    offen = [n for n, f in (aufnahme.get("filter") or {}).items()
             if f["zustand"] == "unbekannt"]
    if offen:
        return False, f"Filter ohne Messergebnis: {', '.join(offen)}"
    if not aufnahme.get("kanarienvogel"):
        return False, "Kanarienvogel nicht gemessen — Codewerte wären nicht deutbar"
    if not aufnahme.get("kontrolle"):
        return False, ("Kontrollprobe nicht gemessen — ohne sie ist „Filter wird "
                       "ignoriert“ nicht belegbar")
    if aufnahme.get("praefixsuche") in (None, "unbekannt"):
        return False, "Präfixsuche nicht messbar"
    if not aufnahme.get("schalter"):
        return False, "Schalter der öffentlichen Seite nicht gelesen"
    return True, ""


@dataclass
class Aenderung:
    """Ein Unterschied zwischen zwei Aufnahmen."""

    schwere: str   # 'kritisch' | 'auffaellig' | 'hinweis'
    bereich: str
    text: str
    #: Was im Werkzeug davon abhängt. Ohne diesen Satz ist eine Meldung nur eine
    #: Beobachtung, und niemand weiß, ob sie etwas bedeutet.
    folge: str = ""


def erhebe(client: Any, pause_s: float = PAUSE_S,
           melde: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Nimmt den Zustand der Schnittstelle auf. Rein lesend, rund 34 Anfragen.

    Jeder Fehlschlag wird als Ergebnis festgehalten statt geworfen: Eine API, die
    heute nicht antwortet, ist ein Befund und kein Grund, die Aufnahme zu
    verlieren.
    """
    sagen = melde or (lambda _t: None)

    def pause() -> None:
        time.sleep(pause_s)

    def zaehle(**kwargs: Any) -> int | None:
        try:
            return client.count_devices(device_status=None, **kwargs)
        except Exception as exc:  # noqa: BLE001 - ein Fehler ist hier ein Messwert
            log.info("Zählabfrage fehlgeschlagen (%s): %s", kwargs, exc)
            return None
        finally:
            pause()

    def zustand(werte: dict[str, int | None], gesamt: int | None) -> str:
        """Filtert dieser Parameter — gemessen am Unterschied seiner Werte?

        Bewusst ohne Bezug auf eine Referenzgruppe: Der Vergleich der Werte
        untereinander braucht keinen stabilen Bezugspunkt und keine Gruppe, in
        der der Filter überhaupt etwas wegnimmt (siehe FILTER).
        """
        zahlen = [n for n in werte.values() if n is not None]
        if not gesamt or len(zahlen) < 2:
            return "unbekannt"
        spielraum = gesamt * TOLERANZ
        if max(zahlen) - min(zahlen) > spielraum:
            return "wirkt"
        # Alle Werte liefern dasselbe. Zwei Möglichkeiten, und beide sind ein
        # Ausfall: Der Parameter wird verworfen (dann ist es der Gesamtbestand)
        # oder er trifft immer dasselbe.
        return "ignoriert"

    aufnahme: dict[str, Any] = {
        "format": FORMAT,
        "zeitpunkt": _jetzt(),
        "build": None,
        "basis": None,
        "gesamt": None,
        "kanarienvogel": None,
        "kontrolle": None,
        "schalter": {},
        "filter": {},
        "wunschliste": {},
        "vokabular": {},
        "felder": {},
        "praefixsuche": None,
        "praefixkette": {},
        "seitenzaehlung": None,
        "seitengroesse": None,
        "offiziell": {},
        "fehler": [],
    }

    # --- Datenstand ---------------------------------------------------------
    sagen("Datenstand")
    try:
        info = client.get_application_info()
        aufnahme["build"] = str((info.data or {}).get("buildVersion") or "")
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"applicationInfo: {_kurz(exc)}")
    pause()

    # --- Basiszahl und Feldnamen der Gerätesuche ----------------------------
    sagen("Gerätesuche")
    basis = None
    try:
        antwort = client.search_devices(cnd_code=REFERENZ_CND, page_size=1,
                                        device_status=None)
        basis = antwort.total_elements
        treffer = (antwort.content or [None])[0]
        aufnahme["felder"]["geraetesuche"] = _felder(treffer)
        aufnahme["geraet_uuid"] = (treffer or {}).get("uuid")
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Gerätesuche: {_kurz(exc)}")
    finally:
        pause()
    aufnahme["basis"] = basis

    # --- Schalter der öffentlichen Seite ------------------------------------
    sagen("Schalter der öffentlichen Seite")
    try:
        antwort = client.request(SCHALTER, {"scope": "PUBLIC",
                                            "languageIso2Code": "en"})
        aufnahme["schalter"] = {
            e["name"]: bool(e.get("enable"))
            for e in (antwort.data or []) if isinstance(e, dict) and e.get("name")}
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Schalter: {_kurz(exc)}")
    pause()

    # --- Der Gesamtbestand als Maßstab --------------------------------------
    # Muss VOR allem anderen stehen: Ohne ihn ist weder die Filterwirkung noch
    # eine Codewert-Trefferzahl deutbar.
    sagen("Gesamtbestand")
    aufnahme["gesamt"] = zaehle()

    # --- Wie reagiert EUDAMED auf einen Wert, den es nicht gibt? ------------
    sagen("Kanarienvogel (erfundener Codewert)")
    kanarie = zaehle(extra_params={KANARIENVOGEL[0]: KANARIENVOGEL[1]})
    if kanarie is None:
        # Kein Messfehler, sondern das Ergebnis: EUDAMED lehnt unbekannte Werte
        # ab (am 2026-08-14 mit HTTP 400). Ein verschwundener Codewert liefert
        # dann keine Zahl, sondern gar nichts — und genau daran ist er zu
        # erkennen.
        aufnahme["kanarienvogel"] = "fehler"
    elif aufnahme["gesamt"] and kanarie >= aufnahme["gesamt"] * (1 - TOLERANZ):
        aufnahme["kanarienvogel"] = "alles"
    else:
        aufnahme["kanarienvogel"] = "null"

    # --- Filter und Codewerte in einem Durchgang ---------------------------
    #
    # Der Kern der ganzen Datei. Je Parameter wird jeder zulässige Wert einzeln
    # gezählt. Unterscheiden sich die Zahlen, filtert der Parameter; sind sie
    # gleich, wird er verworfen. Und jede einzelne Zahl beantwortet zugleich, ob
    # es diesen Codewert überhaupt noch gibt.
    for parameter, eintrag in FILTER.items():
        werte: dict[str, int | None] = {}
        for wert in eintrag["werte"]:
            sagen(f"{parameter}: {wert.rsplit('.', 1)[-1]}")
            werte[wert] = zaehle(extra_params={parameter: wert})
        aufnahme["vokabular"].update(werte)
        aufnahme["filter"][parameter] = {
            "zustand": zustand(werte, aufnahme["gesamt"]),
            "zweck": eintrag["zweck"],
            "treffer": werte,
        }

    # --- Die Kontrollprobe, auf der jedes „wirkt nicht" beruht --------------
    sagen("Kontrollprobe (erfundener Parametername)")
    kontrolle = zaehle(extra_params={KONTROLLE[0]: KONTROLLE[1]})
    aufnahme["kontrolle"] = (
        "verworfen" if kontrolle is not None and aufnahme["gesamt"]
        and abs(kontrolle - aufnahme["gesamt"]) <= aufnahme["gesamt"] * TOLERANZ
        else "abgelehnt" if kontrolle is None else "wirkt")

    # --- Gibt es inzwischen Filter, die es vorher nicht gab? ----------------
    #
    # Die Gegenrichtung zur Filterprüfung oben: Dort geht es darum, dass nichts
    # wegfällt. Hier darum, dass nichts Neues übersehen wird — ein Register im
    # Aufbau bekommt Möglichkeiten dazu, und niemand kündigt sie an.
    for name, eintrag in WUNSCHLISTE.items():
        sagen(f"Wunschliste: {name}")
        anzahl = zaehle(cnd_code=REFERENZ_CND,
                        extra_params={name: eintrag["wert"]})
        if anzahl is None:
            zustand_w = "abgelehnt"
        elif basis is None:
            zustand_w = "unbekannt"
        elif abs(anzahl - basis) > basis * TOLERANZ:
            zustand_w = "wirkt"
        else:
            zustand_w = "verworfen"
        aufnahme["wunschliste"][name] = {"zustand": zustand_w, "treffer": anzahl}

    # --- Kann cndCode noch Präfix-Suche? -----------------------------------
    #
    # Trägt die gesamte Gruppensuche: „alle Dentalprodukte" ist eine Anfrage,
    # solange der Elterncode seine Unterknoten miterfasst. Fiele das weg, müsste
    # der EMDN-Baum lokal expandiert und je Blatt einzeln abgefragt werden — aus
    # einer Anfrage würden dutzende, ohne dass ein Fehler aufträte.
    kette: dict[str, int | None] = {}
    for code in PRAEFIXKETTE:
        sagen(f"Präfixprobe {code}")
        kette[code] = zaehle(cnd_code=code)
    zahlen = [kette[c] for c in PRAEFIXKETTE]
    aufnahme["praefixsuche"] = (
        "unbekannt" if any(z is None for z in zahlen)
        else "ja" if all(z > 0 for z in zahlen)
             and zahlen == sorted(zahlen, reverse=True)
        else "nein")
    aufnahme["praefixkette"] = kette

    # --- Ist die Seitenzählung noch 0-basiert? -----------------------------
    # Wäre sie 1-basiert, läge jede Trefferliste um eine Seite versetzt: Seite 0
    # wäre leer oder gleich Seite 1, und niemand sähe es der Liste an.
    sagen("Seitenzählung")
    try:
        s0 = client.search_devices(cnd_code=REFERENZ_CND, page=0, page_size=5,
                                   device_status=None)
        pause()
        s1 = client.search_devices(cnd_code=REFERENZ_CND, page=1, page_size=5,
                                   device_status=None)
        erste = [g.get("uuid") for g in (s0.content or [])]
        zweite = [g.get("uuid") for g in (s1.content or [])]
        aufnahme["seitenzaehlung"] = (
            "0-basiert" if erste and zweite and erste != zweite else "auffaellig")
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Seitenzählung: {_kurz(exc)}")
    pause()

    # --- Feldnamen der übrigen Bestände ------------------------------------
    uuid = aufnahme.get("geraet_uuid")
    if uuid:
        sagen("Gerätedetail")
        try:
            aufnahme["felder"]["geraetedetail"] = _felder(
                client.get_device(uuid).data)
        except Exception as exc:  # noqa: BLE001
            aufnahme["fehler"].append(f"Gerätedetail: {_kurz(exc)}")
        pause()

        sagen("Basic-UDI (Zertifikate)")
        try:
            aufnahme["felder"]["basic_udi"] = _felder(
                client.get_basic_udi_by_device(uuid).data)
        except Exception as exc:  # noqa: BLE001
            aufnahme["fehler"].append(f"Basic-UDI: {_kurz(exc)}")
        pause()

    sagen("Zertifikatssuche")
    try:
        antwort = client.search_certificates(page_size=1)
        aufnahme["felder"]["zertifikate"] = _felder((antwort.content or [None])[0])
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Zertifikatssuche: {_kurz(exc)}")
    pause()

    sagen("Herstellersuche")
    try:
        antwort = client.search_actors(name="Braun", page_size=1)
        aufnahme["felder"]["hersteller"] = _felder((antwort.content or [None])[0])
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Herstellersuche: {_kurz(exc)}")
    pause()

    # --- Seitengröße --------------------------------------------------------
    # Sinkt die zulässige Seitengröße, verdreifacht sich still die Zahl der
    # Anfragen für dieselbe Trefferliste — und die Zeitschätzung der Oberfläche
    # stimmt nicht mehr.
    sagen("Seitengröße")
    try:
        antwort = client.search_devices(cnd_code=REFERENZ_CND, page_size=300,
                                        device_status=None)
        aufnahme["seitengroesse"] = len(antwort.content or [])
    except Exception as exc:  # noqa: BLE001
        aufnahme["fehler"].append(f"Seitengröße: {_kurz(exc)}")
    pause()

    # --- Die offizielle Schnittstelle ---------------------------------------
    sagen("Offizielle Schnittstelle")
    aufnahme["offiziell"] = _erhebe_offiziell(client, pause, aufnahme["fehler"])

    return aufnahme


def _erhebe_offiziell(client: Any, pause: Callable[[], None],
                      fehler: list[str]) -> dict[str, Any]:
    """Der zweite Zugang. Rund zehn Anfragen.

    Anders als oben wird hier nicht gezählt, sondern **gefragt**: Weil die
    Gegenseite unbekannte Parameter mit HTTP 400 ablehnt, ist jede Zusage eine
    Ja/Nein-Frage statt eines Trefferzahlvergleichs. Das ist die belastbarste
    Messung in dieser Datei — und sie geht in beide Richtungen: Ein abgelehnter
    Parameter, der plötzlich antwortet, ist eine neue Fähigkeit.
    """
    from client.eudamed_client import DATALAKE_URL

    befund: dict[str, Any] = {
        "angenommen": {}, "abgelehnt": {}, "leerzeichen": {},
        "cursor": None, "seitengroesse": None, "referenz": {},
    }

    def frage(params: dict[str, str]) -> tuple[int | None, Any]:
        """(HTTP-Status, Nutzdaten). Ein Fehler ist hier ein Messwert."""
        try:
            antwort = client.request(
                "/udi", {"format": "json", "api-version": "v1.0", **params},
                base=DATALAKE_URL)
            return antwort.status_code, antwort.data
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            return status, None
        finally:
            pause()

    for name, wert in OFFIZIELL_ANGENOMMEN.items():
        status, daten = frage({name: wert})
        saetze = len((daten or {}).get("value") or []) if daten else 0
        befund["angenommen"][name] = {"status": status, "saetze": saetze}
        # Erste Seite: Cursor und Seitengröße nebenbei mitnehmen.
        if daten and befund["cursor"] is None and saetze:
            befund["cursor"] = bool((daten or {}).get("nextLink"))
            befund["seitengroesse"] = saetze

    for name, wert in OFFIZIELL_ABGELEHNT.items():
        status, _ = frage({name: wert})
        befund["abgelehnt"][name] = {"status": status}

    # Der Leerzeichen-Fehler: ohne -> 0 Treffer, mit -> viele.
    ohne, mit = LEERZEICHEN_PROBE
    for etikett, wert in (("ohne", ohne), ("mit", mit)):
        _, daten = frage({"NOMENCLATURE_CODE": wert})
        befund["leerzeichen"][etikett] = len((daten or {}).get("value") or []) \
            if daten else None

    # Führt /reference die Schlüssel noch, auf denen die Normalisierung steht?
    try:
        from client.official_client import OfficialClient

        tabelle = OfficialClient(client).referenztabelle()
        for schluessel in REFERENZ_SCHLUESSEL:
            befund["referenz"][f"{schluessel[0]}:{schluessel[1]}"] = (
                tabelle.get(schluessel) is not None)
    except Exception as exc:  # noqa: BLE001
        fehler.append(f"Referenztabelle: {_kurz(exc)}")
    pause()

    return befund


# ---------------------------------------------------------------------------
# Vergleich
# ---------------------------------------------------------------------------


def _vergleiche_offiziell(alt: dict[str, Any],
                          neu: dict[str, Any]) -> list[Aenderung]:
    """Was sich am zweiten Zugang geändert hat.

    Die Bewertung folgt der Frage, was im Werkzeug kaputtgeht:

      * Ein **angenommener** Parameter, der ausfällt, bricht den Zusatzpfad —
        `kritisch`.
      * Ein **abgelehnter**, der plötzlich antwortet, ist eine neue Fähigkeit —
        `hinweis`, aber mit Handlungsaufforderung: `RISK_CLASS_ID` etwa machte
        Fall C überflüssig.
      * Der **Leerzeichen-Fehler**, der verschwindet, ist gefährlicher als er
        klingt: Die Kapselung in `official_client.py` stellt dem Wert ein
        Leerzeichen voran, und ohne den Fehler findet sie nichts mehr —
        schweigend.
      * Ein fehlender **Referenzschlüssel** lässt die Risikoklasse offiziell
        geholter Geräte auf NULL stehen.
    """
    aenderungen: list[Aenderung] = []
    if not alt or not neu:
        return aenderungen

    for name, jetzt in (neu.get("angenommen") or {}).items():
        vorher = (alt.get("angenommen") or {}).get(name)
        if not vorher:
            continue
        if vorher.get("status") == 200 and jetzt.get("status") != 200:
            aenderungen.append(Aenderung(
                "kritisch", "Offizielle Schnittstelle",
                f"`{name}` wird nicht mehr angenommen "
                f"(HTTP {vorher.get('status')} -> {jetzt.get('status')}).",
                "Der offizielle Zusatzpfad bricht. `client/capabilities.py` "
                "muss den Parameter aus OFFIZIELL.serverfilter nehmen, sonst "
                "läuft jede Abfrage dorthin in einen Fehler."))

    for name, jetzt in (neu.get("abgelehnt") or {}).items():
        vorher = (alt.get("abgelehnt") or {}).get(name)
        if not vorher:
            continue
        if vorher.get("status") != 200 and jetzt.get("status") == 200:
            aenderungen.append(Aenderung(
                "hinweis", "Offizielle Schnittstelle",
                f"`{name}` wird jetzt ANGENOMMEN (HTTP "
                f"{vorher.get('status')} -> 200) — eine neue Fähigkeit.",
                "In OFFIZIELL.serverfilter aufnehmen. Bei RISK_CLASS_ID oder "
                "IMPLANTABLE entfiele damit das lokale Nachfiltern (Fall C)."))

    alt_l, neu_l = alt.get("leerzeichen") or {}, neu.get("leerzeichen") or {}
    if alt_l and neu_l and alt_l.get("ohne") == 0 and (neu_l.get("ohne") or 0) > 0:
        aenderungen.append(Aenderung(
            "kritisch", "Offizielle Schnittstelle",
            "Der Leerzeichen-Fehler in NOMENCLATURE_CODE ist behoben — die "
            f"Abfrage ohne führendes Leerzeichen liefert jetzt "
            f"{neu_l['ohne']} Datensätze statt null.",
            "Die Kapselung in `client/official_client.py:iter_udi` stellt dem "
            "Wert weiterhin ein Leerzeichen voran und findet damit womöglich "
            "nichts mehr. Sie MUSS entfernt werden."))

    for schluessel, da in (neu.get("referenz") or {}).items():
        if (alt.get("referenz") or {}).get(schluessel) and not da:
            aenderungen.append(Aenderung(
                "kritisch", "Offizielle Schnittstelle",
                f"Der Referenzschlüssel `{schluessel}` steht nicht mehr in "
                f"/reference.",
                "Die Zuordnungstabelle in `client/normalisierung.py` läuft "
                "für diesen Wert ins Leere — betroffene Geräte bekämen keine "
                "Risikoklasse mehr, statt einer falschen. Tabelle prüfen."))

    if alt.get("cursor") and not neu.get("cursor"):
        aenderungen.append(Aenderung(
            "auffaellig", "Offizielle Schnittstelle",
            "Die erste Seite trägt kein `nextLink` mehr.",
            "Entweder ist die Paginierung umgestellt (dann holt "
            "`iter_udi` nur noch die erste Seite und schweigt darüber) oder "
            "die Ergebnismenge passt neuerdings auf eine Seite."))

    return aenderungen


def vergleiche(alt: dict[str, Any], neu: dict[str, Any]) -> list[Aenderung]:
    """Was hat sich seit der letzten Aufnahme geändert?

    Die Reihenfolge der Prüfungen ist die Reihenfolge ihrer Wichtigkeit, und die
    Schwere richtet sich danach, was im Werkzeug kaputtgeht — nicht danach, wie
    groß die Änderung aussieht.
    """
    aenderungen: list[Aenderung] = []

    if alt.get("format") != neu.get("format"):
        return [Aenderung(
            "hinweis", "Aufnahme",
            f"Das Format der Aufnahme hat sich geändert "
            f"({alt.get('format')} -> {neu.get('format')}).",
            "Diese Aufnahme gilt als Erstaufnahme; verglichen wird erst wieder "
            "mit der nächsten.")]

    # --- Die offizielle Schnittstelle --------------------------------------
    # Steht vor den Filtern, weil ihre Zusagen hart geprüft sind: Ein 400 statt
    # eines 200 ist eine Tatsache, keine Erschließung aus zwei Zählungen.
    aenderungen += _vergleiche_offiziell(alt.get("offiziell") or {},
                                         neu.get("offiziell") or {})

    # --- Filter: der kritische Teil ----------------------------------------
    for name, neuer in (neu.get("filter") or {}).items():
        alter = (alt.get("filter") or {}).get(name)
        if not alter:
            continue
        if alter["zustand"] == "wirkt" and neuer["zustand"] == "ignoriert":
            aenderungen.append(Aenderung(
                "kritisch", "Filter",
                f"`{name}` wirkt nicht mehr — alle Werte liefern dieselbe "
                f"Trefferzahl.",
                f"Suchen nach {neuer['zweck']} liefern ab sofort ungefilterte "
                f"Listen, ohne Fehlermeldung. Der Parametername hat sich "
                f"vermutlich geändert; die Kandidaten stehen in "
                f"../FILTER_MATRIX.md."))
        elif alter["zustand"] == "ignoriert" and neuer["zustand"] == "wirkt":
            aenderungen.append(Aenderung(
                "auffaellig", "Filter",
                f"`{name}` wirkt wieder.",
                f"Die Einschränkung nach {neuer['zweck']} ist wieder verlässlich."))

    # --- Schalter der öffentlichen Seite -----------------------------------
    #
    # Ein Schalter, der angeht, ist die einzige Meldung dieser Wacht, die eine
    # neue MÖGLICHKEIT ankündigt statt eines Schadens. Deshalb „auffaellig" und
    # nicht „kritisch" — aber mit dem Satz, was daraus folgt.
    for name, jetzt in (neu.get("schalter") or {}).items():
        vorher = (alt.get("schalter") or {}).get(name)
        if vorher is None or vorher == jetzt:
            continue
        folge = SCHALTER_FOLGEN.get(name, "")
        aenderungen.append(Aenderung(
            "auffaellig", "Öffentliche Seite",
            f"`{name}` steht jetzt auf {'AN' if jetzt else 'AUS'} (vorher "
            f"{'AN' if vorher else 'AUS'}).",
            folge or "Was dieser Schalter freigibt, steht in der Beschreibung "
                     "des Parameters — bitte einmal nachsehen."))
    neue_schalter = sorted(set(neu.get("schalter") or {})
                           - set(alt.get("schalter") or {}))
    if neue_schalter and alt.get("schalter"):
        aenderungen.append(Aenderung(
            "hinweis", "Öffentliche Seite",
            f"Neue Schalter: {', '.join(f'`{s}`' for s in neue_schalter)}.",
            "EUDAMED hat der öffentlichen Seite etwas hinzugefügt. Ob es das "
            "Werkzeug betrifft, entscheidet ein Blick in die Beschreibung."))

    # --- Die Kontrollprobe: die Annahme unter allen „wirkt nicht" ----------
    if (alt.get("kontrolle") == "verworfen"
            and neu.get("kontrolle") not in (None, "verworfen")):
        aenderungen.append(Aenderung(
            "auffaellig", "Grundannahme",
            f"EUDAMED verwirft unbekannte Parameter nicht mehr stillschweigend "
            f"(Kontrollprobe: {alt['kontrolle']} -> {neu['kontrolle']}).",
            "Damit bedeutet „Trefferzahl unverändert“ nicht mehr „Filter wird "
            "ignoriert“. Die Aussagen dieser Wacht zu wirkungslosen Parametern "
            "gehören neu bewertet — und ../FILTER_MATRIX.md ebenso."))

    # --- Wunschliste: was es vorher nicht gab -------------------------------
    for name, neuer in (neu.get("wunschliste") or {}).items():
        alter = (alt.get("wunschliste") or {}).get(name)
        if not alter or alter["zustand"] == neuer["zustand"]:
            continue
        if neuer["zustand"] == "wirkt":
            aenderungen.append(Aenderung(
                "auffaellig", "Neuer Filter",
                f"`{name}` wirkt jetzt — vorher {alter['zustand']} "
                f"({neuer['treffer']} statt {alt.get('basis')} Treffer).",
                WUNSCHLISTE.get(name, {}).get("nutzen", "")
                + " Bitte inhaltlich gegenprüfen: Eine veränderte Trefferzahl "
                  "belegt noch nicht, dass richtig gefiltert wird."))
        elif alter["zustand"] == "wirkt":
            aenderungen.append(Aenderung(
                "kritisch", "Neuer Filter",
                f"`{name}` wirkt nicht mehr (jetzt {neuer['zustand']}).",
                "Falls das Werkzeug diesen Filter inzwischen nutzt, sucht es ab "
                "sofort ungefiltert."))

    # --- Präfixsuche und Seitenzählung -------------------------------------
    if alt.get("praefixsuche") == "ja" and neu.get("praefixsuche") == "nein":
        aenderungen.append(Aenderung(
            "kritisch", "Präfixsuche",
            f"`cndCode` erfasst die Unterknoten nicht mehr "
            f"({neu.get('praefixkette')}).",
            "Die gesamte Gruppensuche beruht darauf. „Alle Dentalprodukte“ "
            "liefert ab sofort nur noch die Geräte, die genau auf dem "
            "Elterncode registriert sind — eine sehr viel kürzere Liste, der "
            "man nichts ansieht."))

    if (alt.get("seitenzaehlung") == "0-basiert"
            and neu.get("seitenzaehlung") not in (None, "0-basiert")):
        aenderungen.append(Aenderung(
            "kritisch", "Seitenzählung",
            "Seite 0 und Seite 1 liefern dasselbe — die Zählung ist "
            "offenbar nicht mehr 0-basiert.",
            "Jede mehrseitige Trefferliste wäre um eine Seite versetzt: erste "
            "Seite doppelt, letzte fehlt."))

    # --- Vokabular ----------------------------------------------------------
    #
    # Woran erkennt man einen verschwundenen Codewert? Das hängt davon ab, was
    # EUDAMED mit einem unbekannten Wert macht, und genau das misst der
    # Kanarienvogel bei jedem Lauf mit:
    #
    #   Kanarienvogel == 0        unbekannte Werte filtern -> 0 ist das Signal
    #   Kanarienvogel == gesamt   unbekannte Werte werden ignoriert
    #                             -> „alle Treffer" ist das Signal
    #
    # Ohne diese Unterscheidung wäre die Prüfung im zweiten Fall wirkungslos:
    # Ein verschwundener Wert lieferte dann drei Millionen Treffer und sähe aus
    # wie ein besonders erfolgreicher Filter.
    kanarie, gesamt = neu.get("kanarienvogel"), neu.get("gesamt")
    for wert, anzahl in (neu.get("vokabular") or {}).items():
        vorher = (alt.get("vokabular") or {}).get(wert)
        if vorher is None or not vorher:
            continue
        if kanarie == "fehler":
            verschwunden = anzahl is None
        elif kanarie == "alles":
            verschwunden = (anzahl is not None and gesamt
                            and anzahl >= gesamt * (1 - TOLERANZ))
        else:
            verschwunden = anzahl == 0
        if verschwunden:
            aenderungen.append(Aenderung(
                "kritisch", "Codewert",
                f"`{wert}` kennt EUDAMED nicht mehr "
                f"(vorher {vorher} Treffer, jetzt "
                f"{'keine Antwort' if anzahl is None else f'{anzahl}'}).",
                "Der Wert steht fest im Planner. Eine Frage danach beantwortet "
                "das Werkzeug jetzt mit „nichts gefunden“ — was wie eine "
                "Auskunft aussieht, aber keine ist."))

    if alt.get("kanarienvogel") and kanarie and alt["kanarienvogel"] != kanarie:
        aenderungen.append(Aenderung(
            "auffaellig", "Codewert",
            f"EUDAMED behandelt unbekannte Codewerte anders als bisher "
            f"({alt['kanarienvogel']} -> {kanarie}).",
            "Für sich harmlos, aber es ändert, woran ein verschwundener "
            "Codewert zu erkennen ist. Die Prüfung stellt sich automatisch "
            "darauf ein."))

    # --- Feldnamen ----------------------------------------------------------
    for bestand, felder in (neu.get("felder") or {}).items():
        vorher = set((alt.get("felder") or {}).get(bestand) or [])
        jetzt = set(felder or [])
        if not vorher or not jetzt:
            continue
        verschwunden = sorted(vorher - jetzt)
        dazu = sorted(jetzt - vorher)
        if verschwunden:
            aenderungen.append(Aenderung(
                "kritisch", "Feldnamen",
                f"{bestand}: {len(verschwunden)} Feld(er) verschwunden — "
                + ", ".join(f"`{f}`" for f in verschwunden[:8]),
                "Felder, die der Import liest, kommen leer zurück. Das erzeugt "
                "keine Fehlermeldung, sondern leere Spalten — und eine leere "
                "Spalte liest sich wie „keine Angabe in EUDAMED“."))
        if dazu:
            aenderungen.append(Aenderung(
                "hinweis", "Feldnamen",
                f"{bestand}: {len(dazu)} Feld(er) neu — "
                + ", ".join(f"`{f}`" for f in dazu[:8]),
                "Kein Schaden. Möglicherweise aber eine neue Auskunft, die das "
                "Werkzeug bisher nicht anbietet."))

    # --- Datenstand und Mengen ---------------------------------------------
    if alt.get("build") and neu.get("build") and alt["build"] != neu["build"]:
        aenderungen.append(Aenderung(
            "auffaellig", "Datenstand",
            f"buildVersion {alt['build']} -> {neu['build']}.",
            "Für sich genommen harmlos. Zusammen mit einem der Befunde oben ist "
            "es die Erklärung dafür."))

    alt_basis, neu_basis = alt.get("basis"), neu.get("basis")
    if alt_basis and neu_basis and neu_basis < alt_basis * (1 - RUECKGANG):
        aenderungen.append(Aenderung(
            "auffaellig", "Menge",
            f"Die Referenzgruppe {REFERENZ_CND} ist von {alt_basis} auf "
            f"{neu_basis} Treffer gefallen ({(1 - neu_basis / alt_basis):.0%}).",
            "Ein Register, das verpflichtend wird, schrumpft normalerweise "
            "nicht. Entweder wurde bereinigt, oder die Suche trifft nicht mehr "
            "dasselbe."))

    alt_seite, neu_seite = alt.get("seitengroesse"), neu.get("seitengroesse")
    if alt_seite and neu_seite and neu_seite < alt_seite:
        aenderungen.append(Aenderung(
            "auffaellig", "Seitengröße",
            f"Höchstens {neu_seite} Treffer je Seite statt {alt_seite}.",
            "Dieselbe Trefferliste kostet ab sofort mehr Anfragen. Die "
            "Zeitschätzungen in der Oberfläche sind zu optimistisch."))

    return aenderungen


# ---------------------------------------------------------------------------
# Ablage und Protokoll
# ---------------------------------------------------------------------------


def letzte_aufnahme(verzeichnis: Path = AUFNAHMEN) -> tuple[Path, dict[str, Any]] | None:
    """Die jüngste Aufnahme — oder None, wenn es noch keine gibt."""
    dateien = sorted(verzeichnis.glob("aufnahme_*.json"))
    for pfad in reversed(dateien):
        try:
            return pfad, json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Aufnahme %s nicht lesbar: %s", pfad, exc)
    return None


def faellig(verzeichnis: Path = AUFNAHMEN,
            abstand_tage: int = ABSTAND_TAGE) -> tuple[bool, float | None]:
    """Ist eine neue Aufnahme fällig? Rückgabe: (fällig, Tage seit der letzten)."""
    vorher = letzte_aufnahme(verzeichnis)
    if vorher is None:
        return True, None
    try:
        gemessen = datetime.fromisoformat(vorher[1]["zeitpunkt"])
    except (KeyError, ValueError):
        return True, None
    tage = (datetime.now(timezone.utc) - gemessen).total_seconds() / 86400
    return tage >= abstand_tage, tage


def _protokolliere(aenderungen: list[Aenderung], aufnahme: dict[str, Any],
                   tage: float | None, pfad: Path = PROTOKOLL) -> None:
    """Hängt einen Abschnitt an das Protokoll. Auch „nichts geändert" wird notiert.

    Das ist kein Formalismus: Ein Protokoll, in dem nur Änderungen stehen, lässt
    offen, ob in der Zwischenzeit nichts passiert ist oder nur niemand
    nachgesehen hat. Der Unterschied ist derselbe wie zwischen „keine Angabe in
    EUDAMED“ und „nicht abgefragt“, und er zieht sich durch dieses ganze Projekt.
    """
    datum = aufnahme["zeitpunkt"][:10]
    if tage is None:
        abstand = "Erstaufnahme"
    else:
        abstand = (f"{tage:.0f} Tag seit der letzten Aufnahme" if round(tage) == 1
                   else f"{tage:.0f} Tage seit der letzten Aufnahme")
    schwer = sum(1 for a in aenderungen if a.schwere == "kritisch")

    # Ein Formatwechsel ist kein Befund über EUDAMED, sondern über diese Datei:
    # Verglichen wurde nichts. Dann gehört der Ausgangszustand noch einmal
    # aufgeschrieben — sonst steht im Protokoll eine Aufnahme, deren neue
    # Prüfungen nirgends festgehalten sind.
    neubeginn = tage is None or any(a.bereich == "Aufnahme" for a in aenderungen)

    kopf = f"## {datum} — "
    if not aufnahme.get("brauchbar", True):
        kopf += "Aufnahme unvollständig, verworfen"
    elif tage is None:
        kopf += "Erstaufnahme"
    elif not aenderungen:
        kopf += "keine Änderungen"
    else:
        kopf += f"{len(aenderungen)} Änderung(en)"
        if schwer:
            kopf += f", davon **{schwer} kritisch**"

    zeilen = ["", kopf, "",
              f"*{abstand}. Datenstand {aufnahme.get('build') or 'unbekannt'}, "
              f"Referenzgruppe {REFERENZ_CND}: {aufnahme.get('basis')} Treffer.*", ""]

    if aufnahme.get("fehler"):
        zeilen += ["Nicht messbar in diesem Lauf:", ""]
        zeilen += [f"- {f}" for f in aufnahme["fehler"]] + [""]

    if not aufnahme.get("brauchbar", True):
        zeilen += [
            f"**{aufnahme.get('mangel')}** — diese Aufnahme wird nicht als "
            f"Bezugspunkt abgelegt und nicht verglichen. Eine halb gemessene "
            f"Schnittstelle als Ausgangszustand wäre schlimmer als keiner: Was "
            f"nie gemessen wurde, kann später nicht als ausgefallen auffallen.",
            "", "Die Aufnahme bleibt fällig und wird beim nächsten Start "
            "wiederholt.", ""]
    elif neubeginn:
        wirkt = [n for n, f in (aufnahme.get("filter") or {}).items()
                 if f["zustand"] == "wirkt"]
        neue = [n for n, w in (aufnahme.get("wunschliste") or {}).items()
                if w["zustand"] == "wirkt"]
        zeilen += [
            "Ausgangszustand festgehalten. Verglichen wird ab der nächsten "
            "Aufnahme.", "",
            f"- Wirksame Filter: {', '.join(f'`{n}`' for n in wirkt) or 'keine'}",
            f"- Geprüfte Codewerte: {len(aufnahme.get('vokabular') or {})}",
            f"- Präfixsuche über `cndCode`: {aufnahme.get('praefixsuche')} "
            f"{aufnahme.get('praefixkette')}",
            f"- Seitenzählung: {aufnahme.get('seitenzaehlung')}, "
            f"höchstens {aufnahme.get('seitengroesse')} Treffer je Seite",
            f"- Schalter der öffentlichen Seite: "
            + ", ".join(f"{n}={'an' if w else 'AUS'}"
                        for n, w in sorted((aufnahme.get("schalter") or {}).items())),
            f"- Unbekannter Parametername wird: {aufnahme.get('kontrolle')} · "
            f"unbekannter Codewert: {aufnahme.get('kanarienvogel')}",
            f"- Aus der Wunschliste wirksam: "
            + (", ".join(f"`{n}`" for n in neue) if neue
               else f"keiner von {len(aufnahme.get('wunschliste') or {})}"),
            f"- Erfasste Feldnamen: "
            + ", ".join(f"{b} ({len(f)})"
                        for b, f in (aufnahme.get("felder") or {}).items()),
            ""]
    elif not aenderungen:
        zeilen += ["Filter, Codewerte, Feldnamen, Präfixsuche, Seitenzählung und "
                   "Wunschliste unverändert.", ""]
    else:
        for a in sorted(aenderungen, key=lambda x: ("kritisch", "auffaellig",
                                                    "hinweis").index(x.schwere)):
            zeilen.append(f"- **{a.schwere}** · {a.bereich}: {a.text}")
            if a.folge:
                zeilen.append(f"  → {a.folge}")
        zeilen.append("")

    kopfzeile = ("# Änderungen an der EUDAMED-Schnittstelle\n\n"
                 "Automatisch geführt von `apiwacht.py`. Neueste Einträge unten.\n"
                 "Was hier steht, ist gemessen — was daraus folgt, entscheidet ein "
                 "Mensch.\n")
    bestand = pfad.read_text(encoding="utf-8") if pfad.is_file() else kopfzeile
    pfad.write_text(bestand + "\n".join(zeilen), encoding="utf-8")


def laufe(client: Any, *, verzeichnis: Path = AUFNAHMEN,
          protokoll: Path = PROTOKOLL, pause_s: float = PAUSE_S,
          melde: Callable[[str], None] | None = None,
          ) -> tuple[dict[str, Any], list[Aenderung]]:
    """Eine Aufnahme: messen, vergleichen, ablegen, protokollieren.

    Eine unvollständige Aufnahme wird **nicht** zum Bezugspunkt. Sie landet
    unter anderem Namen (`unbrauchbar_*.json`), den `letzte_aufnahme()` nicht
    findet, und im Protokoll steht, was fehlte. Beim nächsten Start ist die
    Aufnahme dann weiterhin fällig — was genau richtig ist: Gemessen wurde ja
    nichts.
    """
    vorher = letzte_aufnahme(verzeichnis)
    _, tage = faellig(verzeichnis)

    aufnahme = erhebe(client, pause_s=pause_s, melde=melde)
    brauchbar, mangel = vollstaendig(aufnahme)
    aufnahme["brauchbar"] = brauchbar
    if not brauchbar:
        aufnahme["mangel"] = mangel

    aenderungen = (vergleiche(vorher[1], aufnahme)
                   if vorher and brauchbar else [])

    verzeichnis.mkdir(parents=True, exist_ok=True)
    name = "aufnahme" if brauchbar else "unbrauchbar"
    ziel = verzeichnis / f"{name}_{aufnahme['zeitpunkt'][:10]}.json"
    ziel.write_text(json.dumps(aufnahme, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    _protokolliere(aenderungen, aufnahme, tage if vorher else None, protokoll)
    return aufnahme, aenderungen


def starte_im_hintergrund(client_bauen: Callable[[], Any], *,
                          abstand_tage: int = ABSTAND_TAGE,
                          verzoegerung_s: float = VERZOEGERUNG_S,
                          erzwingen: bool = False) -> threading.Thread | None:
    """Startet die Wacht nebenher, wenn sie fällig ist. Sonst passiert nichts.

    Drei Zusagen, und alle drei sind wichtiger als die Messung selbst:

    1. **Der Start der Oberfläche wartet nie.** Eigener Thread, `daemon=True`.
    2. **Ein Fehlschlag bleibt folgenlos.** Was hier schiefgeht, darf die
       Recherche nicht stören — ein Werkzeug, das wegen seiner eigenen
       Selbstüberwachung nicht startet, wäre eine sehr dumme Art zu scheitern.
    3. **Eigener Client.** Nicht der der Sitzung: Der steht unter deren Sperre,
       und die Wacht hätte sie sonst eine Minute lang in der Hand.

    Rückgabe: der Thread, oder None, wenn keine Aufnahme fällig war.
    """
    ist_faellig, tage = faellig(abstand_tage=abstand_tage)
    if not (ist_faellig or erzwingen):
        print(f"API-Wacht: letzte Aufnahme vor {tage:.0f} Tagen, "
              f"nächste in {abstand_tage - tage:.0f} Tagen.")
        return None

    def arbeite() -> None:
        time.sleep(verzoegerung_s)
        try:
            _, aenderungen = laufe(client_bauen())
        except Exception as exc:  # noqa: BLE001 - siehe Zusage 2
            log.warning("API-Wacht fehlgeschlagen: %s", exc)
            print(f"API-Wacht: Aufnahme fehlgeschlagen ({exc}). "
                  f"Die Recherche ist davon nicht betroffen.")
            return
        kritisch = [a for a in aenderungen if a.schwere == "kritisch"]
        if kritisch:
            print(f"\nAPI-WACHT: {len(kritisch)} KRITISCHE ÄNDERUNG(EN) an der "
                  f"EUDAMED-Schnittstelle:")
            for a in kritisch:
                print(f"  · {a.bereich}: {a.text}")
            print(f"  Einzelheiten in {PROTOKOLL.name}\n")
        else:
            print(f"API-Wacht: Aufnahme fertig, {len(aenderungen)} Änderung(en), "
                  f"keine kritische. Protokoll: {PROTOKOLL.name}")

    grund = "keine frühere Aufnahme" if tage is None else f"letzte vor {tage:.0f} Tagen"
    print(f"API-Wacht: Aufnahme fällig ({grund}) — läuft in {verzoegerung_s:.0f} s "
          f"nebenher, rund 34 Anfragen.")
    thread = threading.Thread(target=arbeite, name="apiwacht", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(WURZEL))
    from client import EudamedClient

    parser = argparse.ArgumentParser(
        description="Nimmt den Zustand der EUDAMED-Schnittstelle auf und "
                    "protokolliert Änderungen in docs/changelog.md.")
    parser.add_argument("--jetzt", action="store_true",
                        help="sofort aufnehmen, auch wenn keine Aufnahme fällig ist")
    args = parser.parse_args()

    ist_faellig, tage = faellig()
    if not (ist_faellig or args.jetzt):
        print(f"Letzte Aufnahme vor {tage:.0f} Tagen, nächste in "
              f"{ABSTAND_TAGE - tage:.0f} Tagen. Erzwingen mit --jetzt.")
        sys.exit(0)
    # Eigener Client ohne Lese-Cache: Die Wacht soll messen, nicht erinnern.
    _, aenderungen = laufe(EudamedClient(cache_max_age_s=0), melde=print)
    kritisch = [a for a in aenderungen if a.schwere == "kritisch"]
    print(f"Fertig: {len(aenderungen)} Änderung(en), davon {len(kritisch)} kritisch. "
          f"Protokoll: {PROTOKOLL.relative_to(WURZEL)}")
    sys.exit(2 if kritisch else 0)
