"""Gemeinsame Infrastruktur für die Probe-Skripte.

Jede Probe stellt genau eine Frage an die Schnittstelle und endet mit einem
Verdikt. Die Fragen stammen aus der Sichtung der beiden Referenz-Repos
(openregulatory/eudamed-api, Delapro/EUDAMED) — siehe README.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from client import EudamedError, EudamedHTTPError, EudamedResponse

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class Verdict(str, Enum):
    RESOLVED = "GEKLÄRT"
    PARTIAL = "TEILWEISE"
    OPEN = "OFFEN"
    ERROR = "FEHLER"


@dataclass
class ProbeResult:
    probe_id: str
    title: str
    question: str
    verdict: Verdict = Verdict.OPEN
    findings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    requests_made: int = 0

    def add(self, text: str) -> None:
        self.findings.append(text)

    def conclude(self, verdict: Verdict, text: str) -> None:
        self.verdict = verdict
        self.findings.append(f"**Fazit:** {text}")


def call(fn: Callable[..., EudamedResponse], *args: Any, **kwargs: Any) -> tuple[EudamedResponse | None, str | None]:
    """Ruft eine Client-Methode auf und fängt erwartbare Fehler ab.

    Ein Fehlschlag ist hier kein Abbruchgrund, sondern selbst ein Messergebnis:
    ein 404 auf einen geratenen Endpunkt beantwortet die Frage genauso wie ein 200.
    """
    try:
        return fn(*args, **kwargs), None
    except EudamedHTTPError as exc:
        return None, f"HTTP {exc.status_code}"
    except EudamedError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - Probes sollen nie den Runner abbrechen
        return None, f"{type(exc).__name__}: {exc}"


def find_keys(obj: Any, needle: str, path: str = "$") -> list[tuple[str, Any]]:
    """Sucht rekursiv nach Schlüsseln, deren Name `needle` enthält (case-insensitiv).

    Wird gebraucht, um in großen Antworten überhaupt erst zu finden, wo eine
    gesuchte ID stecken könnte.
    """
    hits: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}"
            if needle.lower() in key.lower():
                hits.append((here, value))
            hits.extend(find_keys(value, needle, here))
    elif isinstance(obj, list):
        for index, item in enumerate(obj[:5]):  # erste 5 reichen zur Strukturerkennung
            hits.extend(find_keys(item, needle, f"{path}[{index}]"))
    return hits


def find_uuid_values(obj: Any, path: str = "$", limit: int = 40) -> list[tuple[str, str]]:
    """Sammelt alle Werte, die wie eine UUID aussehen."""
    hits: list[tuple[str, str]] = []
    if len(hits) >= limit:
        return hits
    if isinstance(obj, dict):
        for key, value in obj.items():
            hits.extend(find_uuid_values(value, f"{path}.{key}", limit - len(hits)))
    elif isinstance(obj, list):
        for index, item in enumerate(obj[:5]):
            hits.extend(find_uuid_values(item, f"{path}[{index}]", limit - len(hits)))
    elif isinstance(obj, str) and UUID_RE.match(obj):
        hits.append((path, obj))
    return hits[:limit]


def id_kind(value: str | None) -> str:
    if not value:
        return "leer"
    if UUID_RE.match(value):
        return "UUID"
    if ULID_RE.match(value):
        return "ULID"
    return "sonstiges"


def top_level_keys(entry: Any) -> list[str]:
    return sorted(entry.keys()) if isinstance(entry, dict) else []


def non_null_keys(entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return []
    return sorted(k for k, v in entry.items() if v not in (None, [], {}, ""))


def null_keys(entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return []
    return sorted(k for k, v in entry.items() if v in (None, [], {}, ""))
