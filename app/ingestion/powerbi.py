"""Lecteurs des exports Power BI : Stock Situation et Stock Movements.

Chaque lecteur retourne les modèles validés + un rapport lisible. Une erreur
(colonne absente, nombre illisible, doublon) bloque l'utilisation du fichier ;
un avertissement est affiché au planner mais n'empêche pas le calcul.
"""

from __future__ import annotations

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
    # Noms vérifiés sur l'export Power BI réel (07-oct-2026) : « Stock Movement Id »,
    # « Stock », « Product Nr », « Product Type (Prod) », « Stock Mvt Date », « Mvt Code »,
    # « Quantity (Sum) », « Mvt Code Descr (Mvt Cd) ». Les autres noms restent acceptés.
    columns={
        "movement_id": ("movement_id", "Stock Movement Id", "Movement Id", "Movement ID", "Id"),
        "location": ("location", "Stock", "Location", "Boutique", "Stock Location"),
        "sku": ("sku", "Product Nr", "Product", "SKU", "Product Code"),
        "product_type": ("product_type", "Product Type (Prod)", "Product Type", "Type"),
        "movement_date": (
            "movement_date",
            "Stock Mvt Date",
            "Movement Date",
            "Date",
            "Posting Date",
        ),
        "movement_code": ("movement_code", "Mvt Code", "Movement Code", "Movement Type", "Code"),
        "quantity": ("quantity", "Quantity (Sum)", "Quantity", "Qty"),
        "movement_description": (
            "movement_description",
            "Mvt Code Descr (Mvt Cd)",
            "Mvt Code Descr",
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
    lines = _resolve_duplicates(lines, report)
    report.rows_kept = len(lines)
    return lines, report


def _is_empty(line: StockSnapshotLine) -> bool:
    return not any(getattr(line, f) for f in NUMERIC_STOCK_FIELDS)


def _resolve_duplicates(
    lines: list[StockSnapshotLine], report: ValidationReport
) -> list[StockSnapshotLine]:
    """Même SKU deux fois au même emplacement.

    Cas réel : Nessoft a deux produits « 473ECO/B » et «  473ECO/B » (espace au début) ;
    une fois les espaces retirés, ils se confondent. Si une seule des lignes a du stock,
    on garde celle-là (avertissement). Si plusieurs lignes ont du stock, on ne peut pas
    choisir : erreur bloquante.
    """
    groups: dict[tuple[str, str], list[StockSnapshotLine]] = {}
    for line in lines:
        groups.setdefault((line.location, line.sku), []).append(line)
    dupes = {k: g for k, g in groups.items() if len(g) > 1}
    if not dupes:
        return lines
    conflicts = [k for k, g in dupes.items() if sum(not _is_empty(x) for x in g) > 1]
    if conflicts:
        report.add(
            "error",
            "DUPLICATE_SKU",
            f"{len(conflicts)} SKU en double avec du stock sur plusieurs lignes, "
            f"ex. {conflicts[:5]}",
        )
        return lines
    kept: list[StockSnapshotLine] = []
    seen: set[tuple[str, str]] = set()
    for line in lines:
        key = (line.location, line.sku)
        if key not in dupes:
            kept.append(line)
        elif key not in seen:
            seen.add(key)
            group = dupes[key]
            kept.append(next((x for x in group if not _is_empty(x)), group[0]))
    report.add(
        "warning",
        "DUPLICATE_SKU_MERGED",
        f"{len(dupes)} SKU présents deux fois au même emplacement (souvent un espace en trop "
        f"dans le code Nessoft) ; la ligne avec du stock est gardée, ex. {sorted(dupes)[:5]}",
    )
    return kept


def read_movements(
    source: Path | BinaryIO, filename: str
) -> tuple[list[StockMovement], ValidationReport]:
    return movements_from_records(parse_table(read_raw(source, filename), MOVEMENTS_SPEC, filename))


def movements_from_records(parsed: ParsedTable) -> tuple[list[StockMovement], ValidationReport]:
    report = parsed.report
    if not parsed.records:
        return [], report
    raw_dates = [r["movement_date"] for r in parsed.records]
    serial = _excel_serial_dates(raw_dates)
    if serial is not None:
        raw_dates = serial
        msg = "Dates lues comme numéros de série Excel (cellules date sans format)"
        report.add("info", "DATE_SERIAL", msg)
    dates, fmt, err = parse_dates(raw_dates)
    if err:
        report.add("error", "BAD_DATE", err)
        return [], report
    if fmt and fmt != "ISO":
        report.add("info", "DATE_FORMAT", f"Dates lues au format {fmt} (seul format cohérent)")
    quantities = [parse_number(r["quantity"]) for r in parsed.records]
    sign = _quantity_sign(quantities, parsed.records, report)
    if sign == 0:
        return [], report
    moves: list[StockMovement] = []
    bad = []
    for rec, day, raw_qty in zip(parsed.records, dates, quantities, strict=True):
        row = int(rec["_row"])
        if raw_qty is None or day is None:
            bad.append(row)
            continue
        qty = raw_qty * sign
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
    report.rows_kept = len(moves)
    return moves, report


EXCEL_EPOCH = date(1899, 12, 30)


def _excel_serial_dates(values: list[str]) -> list[str] | None:
    """Dates restées en numéros de série Excel (ex. « 46295 » = 2026-09-30).

    Certains lecteurs perdent le format date de la cellule. On ne convertit que si TOUTES
    les valeurs sont des entiers plausibles (années 1955 à 2119) ; sinon on ne touche à rien.
    """
    filled = [v for v in values if v]
    if not filled or not all(v.isdigit() and 20000 <= int(v) <= 80000 for v in filled):
        return None
    return [(EXCEL_EPOCH + timedelta(days=int(v))).isoformat() if v else "" for v in values]


def _quantity_sign(
    quantities: list[float | None], records: list[dict[str, str]], report: ValidationReport
) -> int:
    """Convention de signe des sorties de stock.

    L'export Power BI réel donne les sorties en négatif (filtre « Quantity (Sum) ≤ 0 ») ;
    le calculateur et les données de test les donnent en positif. Les ventes de l'outil
    sont toujours positives : -1 = inverser le signe, 1 = garder, 0 = fichier refusé
    (signes mélangés : impossible de savoir ce qui est une vente).
    """
    values = [q for q in quantities if q is not None]
    negative = [int(r["_row"]) for r, q in zip(records, quantities, strict=True) if q and q < 0]
    positive = [int(r["_row"]) for r, q in zip(records, quantities, strict=True) if q and q > 0]
    if negative and positive:
        report.add(
            "error",
            "MIXED_SIGNS",
            f"Quantités positives ({len(positive)}) et négatives ({len(negative)}) mélangées : "
            "impossible de savoir lesquelles sont des sorties de stock. Exporter seulement les "
            "sorties (filtre Power BI « Quantity ≤ 0 »).",
            positive[:10] + negative[:10],
        )
        return 0
    if negative:
        report.add(
            "info",
            "NEGATIVE_EXITS",
            f"Sorties de stock en négatif dans l'export ({len(values)} lignes) : "
            "comptées comme ventes positives",
        )
        return -1
    return 1


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
