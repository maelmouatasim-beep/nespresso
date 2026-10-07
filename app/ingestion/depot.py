"""Dépôt du jour : date d'extraction des exports et fraîcheur. Fonctions sans écran.

Il n'y a aucune connexion automatique : le planner dépose lui-même ses deux exports.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime

from openpyxl import load_workbook

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
