"""Lecteurs des exports Power BI : Stock Situation et Stock Movements.

Chaque lecteur retourne les modèles validés + un rapport lisible. Une erreur
(colonne absente, nombre illisible, doublon) bloque l'utilisation du fichier ;
un avertissement est affiché au planner mais n'empêche pas le calcul.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import BinaryIO

from app.domain.models import StockMovement, StockSnapshotLine
from app.ingestion.tables import (
    ParsedTable,
    TableSpec,
    ValidationReport,
    parse_dates,
    parse_number,
    parse_table,
    read_raw,
)

STOCK_SITUATION_SPEC = TableSpec(
    name="Stock Situation",
    columns={
        "location": ("Stock", "Location", "Stock Location", "Warehouse"),
        "sku": ("Product", "SKU", "Product Code", "Item"),
        "description": ("Product Descr", "Product Description", "Description"),
        "product_type": ("Product Type", "Type"),
        "expected": ("Expected",),
        "available": ("Available",),
        "incoming": ("Incoming",),
        "reserved": ("Reserved",),
        "waiting": ("Waiting",),
    },
    required=frozenset({"location", "sku", "expected", "available"}),
)

MOVEMENTS_SPEC = TableSpec(
    name="Stock Movements",
    columns={
        "movement_id": ("movement_id", "Movement Id", "Movement ID", "Id"),
        "location": ("location", "Stock", "Location", "Boutique", "Stock Location"),
        "sku": ("sku", "Product", "SKU", "Product Code"),
        "product_type": ("product_type", "Product Type", "Type"),
        "movement_date": ("movement_date", "Movement Date", "Date", "Posting Date"),
        "movement_code": ("movement_code", "Movement Code", "Movement Type", "Code"),
        "quantity": ("quantity", "Quantity", "Qty"),
        "movement_description": (
            "movement_description",
            "Movement Description",
            "Movement Descr",
            "Description",
        ),
    },  # fmt: skip
    required=frozenset({"location", "sku", "movement_date", "movement_code", "quantity"}),
)

NUMERIC_STOCK_FIELDS = ("expected", "available", "incoming", "reserved", "waiting")


def read_stock_situation(
    source: Path | BinaryIO, filename: str
) -> tuple[list[StockSnapshotLine], ValidationReport]:
    parsed = parse_table(read_raw(source, filename), STOCK_SITUATION_SPEC, filename)
    return stock_situation_from_records(parsed)


def stock_situation_from_records(
    parsed: ParsedTable,
) -> tuple[list[StockSnapshotLine], ValidationReport]:
    report = parsed.report
    lines: list[StockSnapshotLine] = []
    bad_numbers, negative, no_type, inconsistent = [], [], [], []
    for rec in parsed.records:
        row = int(rec["_row"])
        values: dict[str, float] = {}
        ok = True
        for f in NUMERIC_STOCK_FIELDS:
            text = rec.get(f, "")
            if f not in rec or (text == "" and f not in ("expected", "available")):
                values[f] = 0.0  # colonne facultative absente : signalé ci-dessous
                continue
            number = parse_number(text)
            if number is None:
                bad_numbers.append(row)
                ok = False
                break
            values[f] = number
        if not ok:
            continue
        if values["expected"] < 0:
            negative.append(row)
        if not rec.get("product_type"):
            no_type.append(row)
        if {"incoming", "waiting"} <= rec.keys() and abs(
            values["available"] + values["incoming"] - values["waiting"] - values["expected"]
        ) > 1e-6:
            inconsistent.append(row)
        lines.append(
            StockSnapshotLine(
                location=rec["location"],
                sku=rec["sku"],
                description=rec.get("description") or None,
                product_type=rec.get("product_type") or None,
                **values,
            )
        )
    missing_cols = [
        f
        for f in ("incoming", "reserved", "waiting")
        if parsed.records and f not in parsed.records[0]
    ]
    if missing_cols:
        report.add(
            "warning", "STOCK_COLUMNS_MISSING", f"Colonnes absentes, prises à 0 : {missing_cols}"
        )
    if bad_numbers:
        report.add(
            "error",
            "BAD_NUMBER",
            f"{len(bad_numbers)} ligne(s) avec un nombre illisible",
            bad_numbers,
        )
    if negative:
        report.add(
            "warning",
            "NEGATIVE_EXPECTED",
            f"{len(negative)} Expected négatif(s) (Waiting > stock) : lignes passées en REVIEW",
            negative,
        )
    if no_type:
        report.add(
            "warning", "EMPTY_PRODUCT_TYPE", f"{len(no_type)} ligne(s) sans Product Type", no_type
        )
    if inconsistent:
        report.add(
            "warning",
            "EXPECTED_FORMULA",
            f"{len(inconsistent)} ligne(s) où Expected ≠ Available + Incoming − Waiting",
            inconsistent,
        )
    dupes = [k for k, n in Counter((x.location, x.sku) for x in lines).items() if n > 1]
    if dupes:
        report.add(
            "error",
            "DUPLICATE_SKU",
            f"{len(dupes)} SKU en double pour un même emplacement, ex. {dupes[:5]}",
        )
    report.rows_kept = len(lines)
    return lines, report


def read_movements(
    source: Path | BinaryIO, filename: str
) -> tuple[list[StockMovement], ValidationReport]:
    parsed = parse_table(read_raw(source, filename), MOVEMENTS_SPEC, filename)
    report = parsed.report
    if not parsed.records:
        return [], report
    dates, fmt, err = parse_dates([r["movement_date"] for r in parsed.records])
    if err:
        report.add("error", "BAD_DATE", err)
        return [], report
    if fmt and fmt != "ISO":
        report.add("info", "DATE_FORMAT", f"Dates lues au format {fmt} (seul format cohérent)")
    moves: list[StockMovement] = []
    bad, negative = [], []
    for rec, day in zip(parsed.records, dates, strict=True):
        row = int(rec["_row"])
        qty = parse_number(rec["quantity"])
        if qty is None or day is None:
            bad.append(row)
            continue
        if qty < 0:
            negative.append(row)
        moves.append(
            StockMovement(
                movement_id=rec.get("movement_id") or f"row{row}",
                location=rec["location"],
                sku=rec["sku"],
                product_type=rec.get("product_type") or None,
                movement_date=day,
                movement_code=rec["movement_code"],
                quantity=qty,
                movement_description=rec.get("movement_description") or None,
            )
        )
    if bad:
        report.add("error", "BAD_NUMBER", f"{len(bad)} mouvement(s) illisible(s)", bad)
    if negative:
        report.add(
            "warning",
            "NEGATIVE_QUANTITY",
            f"{len(negative)} quantité(s) négative(s) : vérifier le signe des sorties de stock",
            negative,
        )
    report.rows_kept = len(moves)
    return moves, report


def check_freshness(
    report: ValidationReport,
    run_date: date,
    *,
    movements: list[StockMovement] | None = None,
    extracted_on: date | None = None,
) -> None:
    """Signale des données trop anciennes par rapport au jour du run."""
    if extracted_on is not None and extracted_on < run_date:
        report.add(
            "warning",
            "STALE_EXTRACT",
            f"Export du {extracted_on.isoformat()}, plus ancien que le run "
            f"du {run_date.isoformat()}",
        )
    if movements:
        last = max(m.movement_date for m in movements)
        if last < run_date - timedelta(days=1):
            report.add(
                "warning",
                "STALE_SALES",
                f"Dernière vente le {last.isoformat()} : il manque des jours avant le run",
            )
        if last >= run_date:
            report.add(
                "warning",
                "SALES_ON_RUN_DAY",
                "Des ventes du jour du run sont incluses (journée incomplète ?)",
            )
