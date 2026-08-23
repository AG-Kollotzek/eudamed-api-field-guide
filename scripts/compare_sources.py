"""Beide EUDAMED-Schnittstellen dieselbe Frage stellen — in derselben Minute.

    python scripts/vergleiche_quellen.py --srn DE-MF-000006183
    python scripts/vergleiche_quellen.py --cnd Q010601
    python scripts/vergleiche_quellen.py --srn DE-MF-000006183 --protokoll

## Warum es diese Sonde gibt

`OFFIZIELLE_API.md` §7 warnt: „Zwei Quellen sind zwei Wahrheiten" und nennt als
Beleg 1081 Datensätze über die offizielle gegen 1071 über die UI-Schnittstelle.
Der Beleg trägt nicht: Die beiden Zahlen wurden **an verschiedenen Tagen**
gemessen, und EUDAMED bekommt täglich Neuregistrierungen. Zehn Datensätze
Unterschied sind dann keine Uneinigkeit, sondern schlicht ein Tag.

Bevor darauf eine Architekturentscheidung gebaut wird — nämlich ob die
Quellenwahl automatisch laufen darf —, gehört die Frage gemessen. Diese Sonde
stellt beiden Schnittstellen **dieselbe Frage im selben Zeitfenster** und
vergleicht die Geräte-UUID-Mengen. Sie beantwortet damit genau eine Frage:

    Ist der Unterschied ein Aktualitätsversatz, oder folgt er aus der
    unterschiedlichen Filtersemantik?

## Warum die beiden Betriebsarten verschieden viel taugen

`--srn` ist der **saubere** Vergleich: `srn` (UI) und `MF_SRN` (offiziell) sind
dieselbe exakte Übereinstimmung auf dieselbe Kennung. Was hier abweicht, ist
Aktualität — sonst nichts. Das ist die Messung, auf die es ankommt.

`--cnd` ist der **semantische** Vergleich und muss abweichen: `cndCode` sucht
mit **Präfix**, `NOMENCLATURE_CODE` matcht **exakt** (und über einen Wert mit
führendem Leerzeichen, siehe `OFFIZIELLE_API.md` §3). Für `Q010601` erfasst die
UI-Schnittstelle deshalb auch `Q01060199` und jedes tiefere Blatt.

Daraus folgt eine **gerichtete** Erwartung, und die ist prüfbar, obwohl die
UI-Suche je Treffer gar keinen EMDN-Code zurückgibt (nachgesehen: 31 Felder,
`cndCode` ist keines davon):

    offiziell ⊆ UI

Ein Datensatz, den nur die UI kennt, ist erwartbar — er hängt an einem tieferen
Blatt. Ein Datensatz, den **nur die offizielle Schnittstelle** kennt, kann
dagegen nicht aus der Semantik folgen: Exaktsuche findet nie etwas, das die
Präfixsuche verfehlt. Genau diese Zahl ist das Signal.

Kostet drei bis vier Anfragen. Kein Sprachmodell, keine Schreibzugriffe auf die
Datenbank — die Sonde misst und schreibt höchstens ihren Bericht.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client import EudamedClient  # noqa: E402
from client.eudamed_client import DATALAKE_URL, MAX_PAGE_SIZE  # noqa: E402

GRUEN, ROT, GELB, GRAU, AUS = ("\033[32m", "\033[31m", "\033[33m",
                               "\033[90m", "\033[0m")

#: Die offizielle Schnittstelle verlangt beide Parameter bei jedem Aufruf.
OFFIZIELL_FEST = {"format": "json", "api-version": "v1.0"}

#: Ab so viel Abweichung im sauberen Vergleich (`--srn`) ist es kein Rauschen
#: mehr. Zwei Registrierungen zwischen zwei Anfragen sind normal; fünf Prozent
#: sind ein Versatz, der eine Betriebsart entscheidet.
SCHWELLE = 0.05

#: Wohin der Bericht geht. Dieselbe Datei, in der die API-Wacht ihre Funde
#: festhält — Beobachtungen über die Schnittstellen gehören an eine Stelle.
PROTOKOLL = Path(__file__).resolve().parent.parent / "docs" / "changelog.md"


# ---------------------------------------------------------------------------
# Abrufen
# ---------------------------------------------------------------------------


def hole_ui(client: EudamedClient, *, srn: str | None = None,
            cnd: str | None = None, grenze: int = 2000) -> dict[str, str]:
    """UUID -> EMDN-Code über die UI-Schnittstelle. Nur Stufe 1, keine Details.

    Bewusst **nicht** über `ingest.sync_devices`: Das würde die Stufen 2 und 3
    auslösen, also eine Anfrage je Gerät. Hier wird gezählt, nicht geladen.
    """
    # Wert ist bewusst leer: Die UI-Suche liefert je Treffer **keinen**
    # EMDN-Code (geprüft am Rohbestand — 31 Felder, `cndCode` ist keines
    # davon). Wer hier `basicUdi` einsetzte, verglichen eine Produktkennung
    # gegen einen Nomenklaturcode und bekäme immer „passt nicht".
    treffer: dict[str, str] = {}
    seite = 0
    while len(treffer) < grenze:
        antwort = client.search_devices(
            cnd_code=cnd, srn=srn, page=seite, page_size=MAX_PAGE_SIZE,
            # Ohne diese Klammer filtert die UI-Suche auf „am Markt" vor und
            # vergleicht damit gegen eine ungefilterte offizielle Liste.
            device_status=None)
        eintraege = antwort.content
        if not eintraege:
            break
        for e in eintraege:
            uuid = e.get("uuid")
            if uuid:
                treffer[uuid] = ""
        if antwort.is_last_page:
            break
        seite += 1
    return treffer


def hole_offiziell(client: EudamedClient, *, srn: str | None = None,
                   cnd: str | None = None, grenze: int = 2000) -> dict[str, str]:
    """UUID -> EMDN-Code über die offizielle Schnittstelle, mit Cursor.

    `NOMENCLATURE_CODE` bekommt das führende Leerzeichen vorangestellt: Die
    gespeicherten Werte tragen es (`" L031299"`, im Rohbestand nachgesehen),
    und ohne das liefert die Abfrage null Zeilen. Das ist ein Fehler der
    Gegenseite und wird hier gekapselt, nicht dem Aufrufer aufgebürdet.
    """
    filter: dict[str, Any] = dict(OFFIZIELL_FEST)
    if srn:
        filter["MF_SRN"] = srn
    if cnd:
        filter["NOMENCLATURE_CODE"] = f" {cnd}"

    treffer: dict[str, str] = {}
    pfad, params = "/udi", filter
    while len(treffer) < grenze:
        antwort = client.request(pfad, params, base=DATALAKE_URL)
        daten = antwort.data if isinstance(antwort.data, dict) else {}
        for satz in daten.get("value") or []:
            uuid = satz.get("UUID")
            if uuid:
                treffer[uuid] = str(satz.get("NOMENCLATURE_CODE") or "").strip()
        weiter = daten.get("nextLink")
        if not weiter:
            break
        # Der Cursor kommt als vollständige URL zurück. Der Transport hängt
        # `base` + Pfad zusammen, also wird hier der Rest abgeschnitten und
        # der undurchsichtige `$after`-Wert als Parameter durchgereicht.
        from urllib.parse import parse_qs, urlparse

        zerlegt = urlparse(weiter)
        pfad = zerlegt.path.split("/eudamed", 1)[-1] or "/udi"
        params = {**filter,
                  **{k: v[0] for k, v in parse_qs(zerlegt.query).items()}}
    return treffer


# ---------------------------------------------------------------------------
# Vergleichen
# ---------------------------------------------------------------------------


def vergleiche(ui: dict[str, str], offiziell: dict[str, str],
               *, praefixlage: bool) -> dict[str, Any]:
    """Die beiden Mengen gegeneinander. Reine Rechnung, kein Netz.

    `praefixlage` sagt, welche Erwartung gilt:

      False (`--srn`)  Beide Seiten matchen exakt dieselbe Kennung. Jede
                       Abweichung in BEIDE Richtungen ist ungeklärt.
      True  (`--cnd`)  Erwartet wird `offiziell ⊆ UI`. Nur-UI-Datensätze sind
                       die Präfixbreite und damit erklärt; nur-offizielle
                       können aus der Semantik NICHT folgen und zählen voll.
    """
    nur_ui = set(ui) - set(offiziell)
    nur_off = set(offiziell) - set(ui)
    gemeinsam = set(ui) & set(offiziell)

    ungeklaert = nur_off if praefixlage else (nur_ui | nur_off)
    basis = max(len(ui), len(offiziell)) or 1

    return {
        "ui": len(ui), "offiziell": len(offiziell), "gemeinsam": len(gemeinsam),
        "nur_ui": len(nur_ui), "nur_offiziell": len(nur_off),
        "praefixlage": praefixlage,
        "ungeklaert": len(ungeklaert),
        "ungeklaert_anteil": len(ungeklaert) / basis,
        "beispiele_nur_ui": sorted(nur_ui)[:5],
        "beispiele_nur_offiziell": sorted(nur_off)[:5],
    }


def bericht(b: dict[str, Any], *, filterlage: str, dauer_s: float) -> str:
    """Der Befund als Markdown-Block — für Terminal und `docs/changelog.md`."""
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    anteil = b["ungeklaert_anteil"]
    urteil = (
        "**Kein messbarer Versatz.** Die Schnittstellen sind sich einig; die "
        "Quellenwahl darf automatisch laufen."
        if anteil == 0 else
        f"**Versatz von {anteil:.1%} ungeklärt.** Unterhalb der Schwelle von "
        f"{SCHWELLE:.0%} — Rauschen aus Neuregistrierungen zwischen den beiden "
        f"Anfragen, die Quellenwahl darf automatisch laufen."
        if anteil < SCHWELLE else
        f"**Versatz von {anteil:.1%} ungeklärt — über der Schwelle von "
        f"{SCHWELLE:.0%}.** Die Betriebsart gehört auf `nur_anreicherung`: "
        f"Die Trefferliste kommt dann immer von der UI-Schnittstelle, die "
        f"offizielle liefert nur die Produktmerkmale nach."
    )
    return "\n".join([
        f"### Quellenvergleich {jetzt}",
        "",
        f"Filterlage: `{filterlage}` · beide Abfragen in {dauer_s:.1f} s",
        "",
        f"| | Datensätze |",
        f"|---|---|",
        f"| UI-Schnittstelle | {b['ui']} |",
        f"| offizielle Schnittstelle | {b['offiziell']} |",
        f"| in beiden | {b['gemeinsam']} |",
        f"| nur UI | {b['nur_ui']}"
        + (" _(erwartet: Präfixbreite)_ |" if b["praefixlage"] else " |"),
        f"| nur offiziell | {b['nur_offiziell']}"
        + (" _(kann nicht aus der Semantik folgen)_ |" if b["praefixlage"]
           else " |"),
        f"| **ungeklärt** | **{b['ungeklaert']}** ({anteil:.1%}) |",
        "",
        urteil,
    ])


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Beide EUDAMED-Schnittstellen dieselbe Frage stellen.")
    gruppe = p.add_mutually_exclusive_group(required=True)
    gruppe.add_argument("--srn", help="Hersteller-SRN — der SAUBERE Vergleich: "
                                      "beide Seiten matchen exakt, Abweichung "
                                      "= Aktualität")
    gruppe.add_argument("--cnd", help="EMDN-Code — der SEMANTISCHE Vergleich: "
                                      "UI sucht mit Präfix, offiziell exakt")
    p.add_argument("--grenze", type=int, default=2000,
                   help="Höchstzahl Datensätze je Seite (Vorgabe 2000)")
    p.add_argument("--protokoll", action="store_true",
                   help=f"Befund an {PROTOKOLL.name} anhängen")
    p.add_argument("--no-cache", action="store_true",
                   help="Zwischenspeicher umgehen — für den Vergleich in "
                        "derselben Minute ist das der Regelfall")
    args = p.parse_args(argv)

    wurzel = Path(__file__).resolve().parent.parent
    client = EudamedClient(raw_cache_dir=wurzel / "raw_cache",
                           use_cache=not args.no_cache)
    filterlage = f"srn={args.srn}" if args.srn else f"cnd={args.cnd}"

    print(f"\n{GRAU}— Quellenvergleich: {filterlage} —{AUS}\n")
    start = datetime.now(timezone.utc)
    try:
        print("  UI-Schnittstelle …", end=" ", flush=True)
        ui = hole_ui(client, srn=args.srn, cnd=args.cnd, grenze=args.grenze)
        print(f"{len(ui)} Datensätze")

        print("  offizielle Schnittstelle …", end=" ", flush=True)
        off = hole_offiziell(client, srn=args.srn, cnd=args.cnd,
                             grenze=args.grenze)
        print(f"{len(off)} Datensätze")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  {ROT}Abruf fehlgeschlagen:{AUS} {exc}", file=sys.stderr)
        return 1

    dauer = (datetime.now(timezone.utc) - start).total_seconds()
    b = vergleiche(ui, off, praefixlage=bool(args.cnd))
    text = bericht(b, filterlage=filterlage, dauer_s=dauer)
    print()
    print("\n".join("  " + z for z in text.splitlines()))

    if b["beispiele_nur_ui"]:
        # In der Präfixlage sind diese Datensätze gerade ERKLÄRT — sie hängen
        # an einem tieferen Blatt. Sie „ungeklärt" zu nennen wäre die Sorte
        # Etikett, die eine Messung falsch aussehen lässt.
        etikett = ("erwartet, Präfixbreite" if b["praefixlage"]
                   else "ungeklärt")
        print(f"\n  {GRAU}Beispiele nur UI ({etikett}):{AUS} "
              + ", ".join(b["beispiele_nur_ui"]))
    if b["beispiele_nur_offiziell"]:
        print(f"  {GRAU}Beispiele nur offiziell:{AUS} "
              + ", ".join(b["beispiele_nur_offiziell"]))

    if args.protokoll:
        with PROTOKOLL.open("a", encoding="utf-8") as f:
            f.write("\n\n" + text + "\n")
        print(f"\n  {GRUEN}Befund angehängt an {PROTOKOLL.name}{AUS}")

    farbe = GRUEN if b["ungeklaert_anteil"] < SCHWELLE else GELB
    print(f"\n{farbe}Ungeklärte Abweichung: {b['ungeklaert_anteil']:.1%}{AUS}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
