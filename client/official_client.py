"""Die offizielle EUDAMED-Schnittstelle — dokumentiert, ohne Schlüssel, mit Vertrag.

    from client import EudamedClient
    from client.official_client import OfficialClient

    offiziell = OfficialClient(EudamedClient())
    for satz in offiziell.iter_udi(MF_SRN="DE-MF-000006183"):
        ...                                  # 406 Geräte aus EINER Anfrage
    codes = offiziell.referenztabelle()      # -203.0 -> "class-iii"

Basis: `https://api.datalake.sante.service.ec.europa.eu/eudamed` (siehe
`../OFFIZIELLE_API.md`). Kein Schlüssel, kein Login.

## Warum das kein zweiter Client ist, sondern ein Aufsatz

`EudamedClient` bündelt HTTP, Wiederholversuche, Backoff, Rohdatenspeicher und
das Ereignisprotokoll — rund 250 Zeilen Maschinerie, die **beide**
Schnittstellen brauchen. Ein zweiter, gleichrangiger Client hieße, sie zu
verdoppeln oder erst umzuziehen; beides ist Aufwand an laufendem Code ohne
funktionalen Gewinn.

Stattdessen komponiert dieser Aufsatz den vorhandenen Transport:
`EudamedClient.request()` nimmt seit jeher ein `base=`-Argument, und
`RawCache._slug` bildet den Datalake-Host bereits auf einen eigenen Ordner ab.
Der Zwischenspeicher, das Ereignisprotokoll und die Drosselung gelten damit
unverändert — ohne dass hier eine Zeile davon steht.

## Drei Eigenheiten der Gegenseite, die hier gekapselt werden

**1. Zwei Pflichtparameter.** `format` und `api-version` müssen bei **jedem**
Aufruf mit. Fehlt einer, kommt HTTP 400. Kein Aufrufer sollte das wissen
müssen.

**2. Das führende Leerzeichen.** Die gespeicherten Nomenklaturwerte tragen
eines: `" L031299"`, im Rohbestand nachgesehen. `NOMENCLATURE_CODE=Q010601`
liefert deshalb **null** Zeilen, `NOMENCLATURE_CODE=%20Q010601` liefert 1081.
Das ist ein Fehler der Gegenseite. Er wird hier eingepackt und nicht
weitergereicht — und `apiwacht.py` sieht nach, ob er noch besteht: Verschwindet
er, muss diese Kapselung weg, sonst sucht sie ins Leere.

**3. Cursor statt Seitenzahlen.** `page`, `offset`, `limit`, `$top` — alle acht
geprüften Spielarten antworten mit HTTP 400. Stattdessen trägt jede Antwort ein
`nextLink` mit einem undurchsichtigen `$after`-Wert. Eine Seite fasst 1000
Datensätze.

## Was sie nicht kann

**Keine Zertifikate** — nicht als Filter, nicht als Feld, nicht als Ressource
(`/certificates` antwortet mit 404, Probe 07). **Keine Präfixsuche** über die
Nomenklatur. **Keine Filter** auf Risikoklasse, Rechtsrahmen oder Marktstatus;
die stehen nur als Kennzahlen im Datensatz und sind lokal auszuwerten. Wann
sich der Weg hierher lohnt, muss der Aufrufer entscheiden — nicht dieser Client.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

from client.eudamed_client import DATALAKE_URL, EudamedClient

log = logging.getLogger(__name__)

#: Bei jedem Aufruf verlangt. Fehlt einer, antwortet die Schnittstelle mit 400.
PFLICHTPARAMETER = {"format": "json", "api-version": "v1.0"}

#: Parameter, die `/udi` annimmt. Alles andere wird mit HTTP 400 abgelehnt —
#: das Gegenteil des stillen Verwurfs der UI-API und ein Sicherheitsgewinn:
#: Ein Tippfehler im Parameternamen fällt sofort auf, statt stillschweigend
#: ungefilterte Ergebnisse zu liefern.
ANGENOMMEN = frozenset({
    "PRIMARY_DI", "BASIC_UDI", "MF_SRN", "TRADE_NAME", "DEVICE_NAME",
    "NOMENCLATURE_CODE", "REFERENCE",
    # Am 2026-08-17 nachgemessen und in OFFIZIELLE_API.md §3 falsch als
    # abgelehnt geführt: Beide filtern serverseitig, nur über die eigenen
    # Kennzahlen (`-10.0` statt `class-iii`).
    "RISK_CLASS_ID", "APPLICABLE_LEGISLATION_ID",
})

#: Geprüft und abgelehnt (HTTP 400). Steht hier, damit niemand sie erneut
#: probiert, und damit `apiwacht.py` merkt, wenn eine davon plötzlich geht.
ABGELEHNT = frozenset({
    "MF_NAME", "IMPLANTABLE", "STERILE", "UUID", "DEVICE_STATUS_TYPE_ID",
})

#: So viele Datensätze liefert eine Seite. Nicht einstellbar — jeder Versuch,
#: die Seitengröße zu setzen, endet in HTTP 400.
SEITENGROESSE = 1000

#: Notbremse gegen einen Cursor, der nicht endet. Bei 1000 Datensätzen je Seite
#: sind das 50.000 Geräte; darüber ist die Frage falsch gestellt und nicht die
#: Schleife kaputt.
MAX_SEITEN = 50

#: Obergrenze für den Akteursabzug. Bei 1.000 Sätzen je Seite und rund 48.800
#: Akteuren (gemessen 2026-08-19) sind 49 Seiten nötig; der Aufschlag lässt
#: Wachstum zu, ohne dass ein Fehler in der Cursor-Kette endlos blättert.
AKTEURSSEITEN = 80


def als_filter(parsed: Any) -> dict[str, str]:
    """`ParsedQuery` -> die Parameternamen der offiziellen Schnittstelle.

    Zwei Abbildungen für zwei Schnittstellen — die
    Namensräume überschneiden sich in **keinem** Feld (`srn` gegen `MF_SRN`,
    `tradeName` gegen `TRADE_NAME`), und eine gemeinsame Funktion müsste
    deshalb ohnehin verzweigen.

    Übernommen wird nur, was die Gegenseite auch annimmt. Risikoklasse,
    Rechtsrahmen und Marktstatus fehlen mit Absicht: Sie werden mit HTTP 400
    abgelehnt und sind lokal nachzufiltern.
    """
    filter: dict[str, str] = {}
    if getattr(parsed, "manufacturer_srn", None):
        filter["MF_SRN"] = parsed.manufacturer_srn
    if getattr(parsed, "product_name", None):
        filter["TRADE_NAME"] = parsed.product_name
    if getattr(parsed, "emdn_code", None):
        # Kommt über `route_request` nicht vor — eine EMDN-Abfrage geht immer
        # zur UI-API. Steht hier trotzdem, damit ein direkter Aufruf nicht
        # stillschweigend ohne Gruppenfilter läuft.
        filter["NOMENCLATURE_CODE"] = parsed.emdn_code

    # Risikoklasse und Rechtsrahmen filtern serverseitig — aber nur über die
    # Kennzahlen der Gegenseite. `class-iii` wird mit HTTP 400 abgelehnt,
    # `-10.0` liefert die vier Klasse-III-Geräte von Brainlab. Das ist der
    # Grund, warum `OfficialClient.referenztabelle()` keine Zugabe ist.
    from client.normalisierung import RECHTSRAHMEN_ID, RISIKOKLASSEN_ID

    for feld, tabelle, ziel in (
            ("risk_class", RISIKOKLASSEN_ID, "RISK_CLASS_ID"),
            ("legislation", RECHTSRAHMEN_ID, "APPLICABLE_LEGISLATION_ID")):
        wert = getattr(parsed, feld, None)
        kennzahl = tabelle.get(str(wert)) if wert else None
        if kennzahl is not None:
            filter[ziel] = str(kennzahl)
    return filter


class OfficialClient:
    """Aufsatz auf den vorhandenen Transport, nicht dessen Zwilling."""

    def __init__(self, transport: EudamedClient) -> None:
        self.transport = transport

    # -- Der eine Aufruf, über den alles läuft ------------------------------

    def _hole(self, pfad: str, params: dict[str, Any]) -> dict[str, Any]:
        """Eine Anfrage gegen den Datalake. Gibt die Nutzdaten zurück."""
        antwort = self.transport.request(
            pfad, {**PFLICHTPARAMETER, **params}, base=DATALAKE_URL)
        return antwort.data if isinstance(antwort.data, dict) else {}

    # -- Geräte -------------------------------------------------------------

    def iter_udi(self, *, grenze: int | None = None,
                 **filter: str) -> Iterator[dict[str, Any]]:
        """Alle Geräte zu einer Filterlage — über den Cursor, Seite für Seite.

        Ein `NOMENCLATURE_CODE` bekommt das führende Leerzeichen vorangestellt,
        falls es fehlt (siehe Modul-Docstring). Unbekannte Parameternamen
        werden **hier** abgefangen statt draußen in einem HTTP 400: Die
        Gegenseite nennt im Fehlertext zwar den Parameter, aber der Aufrufer
        soll die Liste sehen, nicht raten.
        """
        unbekannt = set(filter) - ANGENOMMEN
        if unbekannt:
            raise ValueError(
                f"Die offizielle Schnittstelle kennt {sorted(unbekannt)} nicht. "
                f"Angenommen werden: {sorted(ANGENOMMEN)}. "
                f"Nachweislich abgelehnt: {sorted(ABGELEHNT & unbekannt)}."
                if ABGELEHNT & unbekannt else
                f"Die offizielle Schnittstelle kennt {sorted(unbekannt)} nicht. "
                f"Angenommen werden: {sorted(ANGENOMMEN)}.")

        params = dict(filter)
        code = params.get("NOMENCLATURE_CODE")
        if code and not str(code).startswith(" "):
            params["NOMENCLATURE_CODE"] = f" {code}"

        pfad, geliefert = "/udi", 0
        for seite in range(MAX_SEITEN):
            daten = self._hole(pfad, params)
            saetze = daten.get("value") or []
            for satz in saetze:
                if grenze is not None and geliefert >= grenze:
                    return
                geliefert += 1
                yield satz

            weiter = daten.get("nextLink")
            if not weiter or not saetze:
                return
            pfad, params = self._cursor(weiter, filter)
            log.debug("Datalake-Cursor: Seite %s, bisher %s Datensätze",
                      seite + 2, geliefert)

        log.warning("Cursor nach %s Seiten abgebrochen (%s Datensätze) — "
                    "die Filterlage ist zu weit gefasst.", MAX_SEITEN, geliefert)

    @staticmethod
    def _cursor(weiter: str, filter: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """Aus dem `nextLink` wieder Pfad und Parameter machen.

        Die Gegenseite liefert eine vollständige URL; der Transport setzt aber
        `base` und Pfad selbst zusammen. Also wird die URL zerlegt und der
        undurchsichtige `$after`-Wert als gewöhnlicher Parameter durchgereicht.

        Die ursprünglichen Filter kommen wieder mit dazu: Der Cursor trägt sie
        zwar in seinem Token, aber darauf zu bauen hieße, sich auf ein Format
        zu verlassen, das niemand zugesagt hat.
        """
        zerlegt = urlparse(weiter)
        pfad = zerlegt.path.split("/eudamed", 1)[-1] or "/udi"
        aus_url = {k: v[0] for k, v in parse_qs(zerlegt.query).items()}
        return pfad, {**filter, **aus_url}

    def hole_geraet(self, primary_di: str) -> dict[str, Any] | None:
        """Ein einzelnes Gerät über seine UDI-DI."""
        return next(iter(self.iter_udi(PRIMARY_DI=primary_di, grenze=1)), None)

    def hole_hersteller(self, srn: str, *,
                        grenze: int | None = None) -> list[dict[str, Any]]:
        """Alle Geräte eines Herstellers. **Der Fall, für den sich das lohnt.**

        Gemessen am 2026-08-16: `DE-MF-000006183` liefert 406 Geräte mit 60
        Feldern in 21,9 Sekunden — **eine** Anfrage. Über die UI-API sind das
        812 Anfragen und rund 22 Minuten, weil dort die Zertifikats- und die
        Detailstufe je Gerät eine Anfrage kosten.
        """
        return list(self.iter_udi(MF_SRN=srn, grenze=grenze))

    # -- Referenzwerte ------------------------------------------------------

    def referenztabelle(self, sprache: str = "en"
                        ) -> dict[tuple[str, float], str]:
        """(Feldname, Kennzahl) -> Beschriftung. **Ohne die ist ein Datensatz
        nicht lesbar.**

        Die offizielle Schnittstelle liefert ihre Aufzählungswerte als negative
        Kennzahlen: `RISK_CLASS_ID = -203.0`, `APPLICABLE_LEGISLATION_ID =
        -197.0`. Was sie bedeuten, steht ausschließlich in `/reference`.

        Drei Eigenheiten, alle am 2026-08-17 nachgemessen und alle
        fehlerträchtig:

        **1. Der Schlüssel ist das Paar, nicht die Zahl.** `-155.0` ist unter
        `PLACED_ON_THE_MARKET_ID` Mosambik und unter `RISK_CLASS_ID` die
        IVD-Anhang-II-Liste-A. Wer nur nach der Zahl schlüsselt, mischt Länder
        mit Risikoklassen.

        **2. Die Tabelle ist selbst paginiert.** Sie umfasst 6.718 Zeilen über
        sieben Seiten; die erste liefert genau 1.000. Wer den Cursor übersieht,
        bekommt eine Tabelle, die vollständig aussieht und 292 Schlüssel auf
        etwa 260 verkürzt.

        **3. `VALUE` ist eine Übersetzung, kein Bezeichner.** „Classe III",
        „Klasse IIb", „Κατηγορία III" — alles derselbe Wert. Für die
        Zuordnung auf das Vokabular dieses Projekts ist deshalb
        `client/normalisierung.py` zuständig, nicht diese Funktion.
        """
        tabelle: dict[tuple[str, float], str] = {}
        pfad, params = "/reference", {}
        for _ in range(MAX_SEITEN):
            daten = self._hole(pfad, params)
            zeilen = daten.get("value") or []
            for zeile in zeilen:
                feld, kennung = zeile.get("CODE"), zeile.get("ID")
                if not feld or kennung is None:
                    continue
                schluessel = (str(feld), float(kennung))
                # Die gewünschte Sprache gewinnt; sonst gilt der erste Fund,
                # damit auch ohne Übersetzung etwas dasteht.
                if (str(zeile.get("LANGUAGE") or "").lower() == sprache.lower()
                        or schluessel not in tabelle):
                    tabelle[schluessel] = str(zeile.get("VALUE") or "")
            weiter = daten.get("nextLink")
            if not weiter or not zeilen:
                break
            pfad, params = self._cursor(weiter, {})
        return tabelle

    # -- Akteure ------------------------------------------------------------

    def iter_actors(self, *, grenze: int | None = None,
                    seiten: int = AKTEURSSEITEN,
                    pause_s: float = 0.0,
                    **filter: str) -> Iterator[dict[str, Any]]:
        """Der Akteursbestand — über den Cursor, Seite für Seite.

        Am 2026-08-19 erprobt und damit die offene Aufgabe aus
        `OFFIZIELLE_API.md` §8 erledigt: **ohne Filter** liefert der Endpunkt
        1.000 Sätze je Seite samt `nextLink`; bei rund 48.800 Akteuren sind das
        49 Seiten. Der ältere Befund („`NAME=Brainlab` lieferte null Zeilen")
        bleibt richtig — die Filtersemantik ist weiterhin ungeklärt, und genau
        deshalb wird hier ohne Filter geblättert und danach lokal gesucht.

        Warum dieser Weg und nicht die UI-Suche: Der offizielle Abzug ist der
        Weg, auf den die EUDAMED-Oberfläche selbst verweist („Download public
        data on Actors"), er ist versioniert und dokumentiert, er liefert 1.000
        statt 300 Sätze je Anfrage — und er trägt Felder mit, die die UI-Suche
        nur im Detailabruf je Firma hergibt (Webseite, Umsatzsteuernummer)
        sowie den Aktivzustand.

        `pause_s` ist die Höflichkeitspause zwischen den Seiten. Vorgabe 0 —
        wer den ganzen Bestand zieht, setzt sie ausdrücklich (siehe
        `ingest.sync_actors_offiziell`).
        """
        pfad, params, geliefert = "/actors", dict(filter), 0
        for seite in range(seiten):
            daten = self._hole(pfad, params)
            saetze = daten.get("value") or []
            for satz in saetze:
                if grenze is not None and geliefert >= grenze:
                    return
                geliefert += 1
                yield satz

            weiter = daten.get("nextLink")
            if not weiter or not saetze:
                return
            pfad, params = self._cursor(weiter, filter)
            if pause_s:
                time.sleep(pause_s)
            log.debug("Akteurs-Cursor: Seite %s, bisher %s Sätze",
                      seite + 2, geliefert)

        log.warning("Akteursabzug nach %s Seiten abgebrochen (%s Sätze) — "
                    "AKTEURSSEITEN erhöhen.", seiten, geliefert)
