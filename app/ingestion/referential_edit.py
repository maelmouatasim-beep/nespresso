"""Modification d'un référentiel directement dans l'outil (grille modifiable).

Le planner change des cellules, ajoute ou supprime des lignes, puis enregistre. Avant
d'écrire quoi que ce soit, la table est relue par le MÊME lecteur qu'à l'import : une
erreur bloque l'enregistrement avec sa raison (rien de silencieux). Sans pandas : le
même code tourne dans la version web (Pyodide).
"""

from __future__ import annotations

import csv
import io
import tempfile
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from app.ingestion.business_inputs import (
    ALLOCATIONS_SPEC,
    LAUNCHES_SPEC,
    RULES_SPEC,
    TARGETS_SPEC,
    read_allocations,
    read_launches,
    read_rules,
    read_targets,
)
from app.ingestion.fixtures_loader import (
    CONVERSION_COLUMNS,
    DC_MAPPING_COLUMNS,
    MULTIPLE_COLUMNS,
    PORTFOLIO_COLUMNS,
    load_conversions,
    load_multiples,
    read_csv_as_text,
)
from app.ingestion.masters import load_schedule

SCHEDULE_COLUMNS = [
    "boutique", "boutique_name", "deliveries_per_week", "carrier", "order_day",
    "shipping_day", "delivery_day", "cover_days", "notes",
]  # fmt: skip
# Colonnes proposées quand le référentiel n'a pas encore de fichier.
DEFAULT_COLUMNS: dict[str, list[str]] = {
    "multiples": MULTIPLE_COLUMNS,
    "conversions": CONVERSION_COLUMNS,
    "portfolio": PORTFOLIO_COLUMNS,
    "dc_mapping": DC_MAPPING_COLUMNS,
    "schedule": SCHEDULE_COLUMNS,
    "allocations": list(ALLOCATIONS_SPEC.columns),
    "launches": list(LAUNCHES_SPEC.columns),
    "targets": list(TARGETS_SPEC.columns),
    "rules": list(RULES_SPEC.columns),
}
_BUSINESS_READERS = {
    "allocations": read_allocations,
    "launches": read_launches,
    "targets": read_targets,
    "rules": read_rules,
}
# Nom de fichier utilisé pour la vérification (le lecteur cite ce nom dans ses messages).
_CHECK_NAMES = {
    "multiples": "master_multiples.csv",
    "conversions": "master_sku_conversions.csv",
    "portfolio": "master_boutique_portfolio.csv",
    "dc_mapping": "master_dc_mapping.csv",
    "schedule": "master_schedule.csv",
}


def _cell(value: object) -> str:
    """Cellule de la grille en texte : vide pour None / NaN, jamais « nan »."""
    if value is None or (isinstance(value, float) and value != value):  # noqa: PLR0124 - NaN
        return ""
    return str(value)


def table_csv(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """Grille → CSV texte. Les lignes entièrement vides sont retirées."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(list(columns))
    for row in rows:
        cells = [_cell(v) for v in row]
        if any(c.strip() for c in cells):
            w.writerow(cells)
    return buf.getvalue()


def _message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        err = exc.errors()[0]
        where = ".".join(str(x) for x in err.get("loc", ()))
        return f"{where} : {err['msg']}" if where else str(err["msg"])
    return str(exc)


def check_table(key: str, csv_text: str) -> tuple[list[str], list[str]]:
    """Relit la table comme à l'import. Retourne (erreurs bloquantes, avertissements)."""
    if key in _BUSINESS_READERS:
        name = f"{key}.csv"
        _, report = _BUSINESS_READERS[key](io.BytesIO(csv_text.encode("utf-8")), name)
        errors = [i.message + (f" (lignes {', '.join(map(str, i.rows[:5]))})" if i.rows else "")
                  for i in report.issues if i.level == "error"]  # fmt: skip
        warnings = [i.message for i in report.issues if i.level == "warning"]
        return errors, warnings
    if key not in _CHECK_NAMES:
        raise ValueError(f"Référentiel inconnu : {key}")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / _CHECK_NAMES[key]
        path.write_text(csv_text, encoding="utf-8")
        warnings: list[str] = []
        try:
            if key == "multiples":
                _, dupes = load_multiples(path)
                if dupes:
                    warnings.append(f"{len(dupes)} SKU en double ou mal formé(s) : "
                                    + ", ".join(dupes[:10]))  # fmt: skip
            elif key == "conversions":
                load_conversions(path)
            elif key == "schedule":
                _, warnings = load_schedule(path)
            else:
                columns = DC_MAPPING_COLUMNS if key == "dc_mapping" else PORTFOLIO_COLUMNS
                rows = read_csv_as_text(path, columns)
                empty = [i for i, r in enumerate(rows, start=2) if not all(r.values())]
                if empty:
                    return [f"cellule vide aux lignes {empty[:10]}"], []
        except (ValueError, KeyError) as exc:  # SchemaError, date ou nombre illisible
            return [_message(exc)], []
    return [], warnings
