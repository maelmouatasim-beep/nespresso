"""Référentiel des catégories (type produit → catégorie) : lecture et écriture CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

from app.engines.categories import CATEGORIES, normalize_type
from app.ingestion.tables import TableSpec, ValidationReport, parse_table, read_raw

CATEGORY_COLUMNS = ["product_type", "categorie"]
CATEGORIES_SPEC = TableSpec(
    name="Catégories",
    columns={
        "product_type": ("product_type", "Type", "Type produit"),
        "categorie": ("categorie", "Catégorie", "category"),
    },
    required=frozenset({"product_type", "categorie"}),
    sku_fields=frozenset(),
)


def check_mapping(rows: list[tuple[str, str]]) -> tuple[dict[str, str], list[str]]:
    """Valide des couples (type, catégorie). Retourne la table et les erreurs."""
    mapping: dict[str, str] = {}
    errors: list[str] = []
    for i, (raw_type, category) in enumerate(rows, start=1):
        ptype, cat = normalize_type(raw_type), (category or "").strip()
        if not ptype:
            errors.append(f"ligne {i} : type produit vide")
        elif cat not in CATEGORIES:
            errors.append(f"ligne {i} : catégorie « {cat} » inconnue ({' / '.join(CATEGORIES)})")
        elif ptype in mapping and mapping[ptype] != cat:
            errors.append(f"ligne {i} : type {ptype} déjà classé en « {mapping[ptype]} »")
        else:
            mapping[ptype] = cat
    return mapping, errors


def read_categories(
    source: Path | BinaryIO, filename: str
) -> tuple[dict[str, str], ValidationReport]:
    parsed = parse_table(read_raw(source, filename), CATEGORIES_SPEC, filename)
    report = parsed.report
    mapping, errors = check_mapping([(r["product_type"], r["categorie"]) for r in parsed.records])
    for e in errors:
        report.add("error", "INVALID_ROW", e)
    if errors:
        return {}, report
    report.rows_kept = len(mapping)
    return mapping, report


def categories_csv(mapping: Mapping[str, str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CATEGORY_COLUMNS)
    order = {c: i for i, c in enumerate(CATEGORIES)}
    for ptype, cat in sorted(mapping.items(), key=lambda kv: (order[kv[1]], kv[0])):
        w.writerow([ptype, cat])
    return buf.getvalue()
