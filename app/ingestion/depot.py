"""Dépôt du jour : date d'extraction des exports et fraîcheur. Fonctions sans écran.

Il n'y a aucune connexion automatique : le planner dépose lui-même ses deux exports.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime

from app.ingestion.tables import RawTable, TableSpec, parse_table

_PATTERNS = (
    (re.compile(r"(20\d{2})[-_.](\d{2})[-_.](\d{2})"), ("y", "m", "d")),
    (re.compile(r"(\d{2})[-_.](\d{2})[-_.](20\d{2})"), ("d", "m", "y")),
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), ("y", "m", "d")),
)


@dataclass(frozen=True)
class Extraction:
    day: date | None
    how: str  # d'où vient la date, en clair


def date_from_filename(filename: str) -> date | None:
    for pattern, order in _PATTERNS:
        m = pattern.search(filename)
        if m:
            parts = dict(zip(order, (int(g) for g in m.groups()), strict=True))
            try:
                return date(parts["y"], parts["m"], parts["d"])
            except ValueError:
                continue
    return None


def detect_extraction_date(filename: str, data: bytes) -> Extraction:
    """Date d'extraction : d'abord le nom du fichier, sinon les propriétés du classeur Excel."""
    day = date_from_filename(filename)
    if day is not None:
        return Extraction(day, "nom du fichier")
    if filename.lower().endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(data), read_only=True)
            props = wb.properties
            wb.close()
            stamp = props.modified or props.created
            if isinstance(stamp, datetime):
                return Extraction(stamp.date(), "propriétés du fichier Excel")
        except Exception:  # noqa: BLE001 - un fichier illisible est signalé ailleurs
            pass
    return Extraction(None, "non détectée")


@dataclass(frozen=True)
class Freshness:
    level: str  # "green" | "orange" | "red" | "unknown"
    label: str


def freshness(day: date | None, today: date) -> Freshness:
    """Vert si l'export est du jour, orange s'il date d'hier, rouge au-delà."""
    if day is None:
        return Freshness("unknown", "Date d'extraction inconnue : à confirmer")
    age = (today - day).days
    if age <= 0:
        return Freshness("green", "Export du jour")
    if age == 1:
        return Freshness("orange", "Export d'hier")
    return Freshness("red", f"Export de il y a {age} jours")


STOCK_KIND, MOVES_KIND = "stock_situation", "stock_movements"


def detect_kind(filename: str, data: bytes) -> str | None:
    """Reconnaît l'export par ses en-têtes (les deux s'appellent souvent « data - … »).

    Retourne "stock_situation", "stock_movements" ou None si aucun des deux.
    """
    from app.ingestion.powerbi import MOVEMENTS_SPEC, STOCK_SITUATION_SPEC
    from app.ingestion.tables import read_raw

    try:
        head = read_raw(io.BytesIO(data), filename, max_rows=15)
    except Exception:  # noqa: BLE001 - fichier illisible : simplement « non reconnu »
        return None
    found = [
        kind
        for kind, spec in ((STOCK_KIND, STOCK_SITUATION_SPEC), (MOVES_KIND, MOVEMENTS_SPEC))
        if _header_found(head, spec)
    ]
    return found[0] if len(found) == 1 else None


def _header_found(head: RawTable, spec: TableSpec) -> bool:
    report = parse_table(head, spec, "").report
    return not any(i.code == "HEADER_NOT_FOUND" for i in report.issues)
