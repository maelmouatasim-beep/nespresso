"""Tables maîtres (extraites du calculateur par tools/extract_masters.py) et
construction des entrées du moteur pour une boutique."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.domain.models import (
    Product,
    ScheduleSlot,
    SkuConversion,
    StockMovement,
    StockSnapshotLine,
)
from app.engines.conversion import build_conversion_index
from app.engines.forecast import ForecastInputs
from app.engines.sales import aggregate_sales
from app.engines.schedule import parse_weekday
from app.ingestion.fixtures_loader import (
    DC_MAPPING_COLUMNS,
    SchemaError,
    load_conversions,
    load_multiples,
    read_csv_as_text,
)

PORTFOLIO_COLUMNS = ["boutique", "sku", "filter_out"]


@dataclass(frozen=True)
class Masters:
    products: dict[str, Product]
    product_list_anomalies: list[str]
    conversions: list[SkuConversion]
    dc_mapping: dict[str, str]
    portfolio: dict[str, dict[str, str]]  # boutique -> sku -> Yes/No
    sources: dict[str, str] = field(default_factory=dict)
    schedule: list[ScheduleSlot] = field(default_factory=list)
    schedule_issues: list[str] = field(default_factory=list)


def load_schedule(path: Path) -> tuple[list[ScheduleSlot], list[str]]:
    """Créneaux commande -> livraison. Les cover days du fichier sont ignorés.

    Lignes sans jour de commande ignorées ; jours illisibles signalés.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = {"boutique", "order_day", "delivery_day"} - set(df.columns)
    if missing:
        raise SchemaError(f"{path.name} : colonnes manquantes {sorted(missing)}")
    slots: list[ScheduleSlot] = []
    issues: list[str] = []
    for i, r in enumerate(df.to_dict("records"), start=2):
        b, o, d = r["boutique"].strip(), r["order_day"].strip(), r["delivery_day"].strip()
        if not (b and o):
            continue
        ow, dw = parse_weekday(o), parse_weekday(d)
        if ow is None or dw is None:
            issues.append(f"ligne {i} ({b}) : jour illisible « {o} » / « {d} »")
            continue
        carrier = (r.get("carrier") or "").strip() or None
        slots.append(
            ScheduleSlot(boutique=b, order_weekday=ow, delivery_weekday=dw, carrier=carrier)
        )
    return slots, issues


def load_masters_dir(directory: Path) -> Masters:
    """Lit master_multiples.csv, master_sku_conversions.csv, master_dc_mapping.csv et
    les master_boutique_portfolio*.csv d'un dossier."""
    files = {
        "multiples": directory / "master_multiples.csv",
        "conversions": directory / "master_sku_conversions.csv",
        "dc_mapping": directory / "master_dc_mapping.csv",
    }
    missing = [p.name for p in files.values() if not p.exists()]
    if missing:
        raise SchemaError(f"Fichiers maîtres manquants dans {directory} : {missing}")
    products, anomalies = load_multiples(files["multiples"])
    dc_df = read_csv_as_text(files["dc_mapping"], DC_MAPPING_COLUMNS)
    portfolio: dict[str, dict[str, str]] = {}
    for path in sorted(directory.glob("master_boutique_portfolio*.csv")):
        df = read_csv_as_text(path, PORTFOLIO_COLUMNS)
        for b, sku, flag in zip(df["boutique"], df["sku"], df["filter_out"], strict=True):
            portfolio.setdefault(b, {})[sku] = flag
    schedule: list[ScheduleSlot] = []
    schedule_issues: list[str] = []
    if (directory / "master_schedule.csv").exists():
        schedule, schedule_issues = load_schedule(directory / "master_schedule.csv")
    return Masters(
        schedule=schedule,
        schedule_issues=schedule_issues,
        products=products,
        product_list_anomalies=anomalies,
        conversions=load_conversions(files["conversions"]),
        dc_mapping=dict(zip(dc_df["boutique"], dc_df["dc"], strict=True)),
        portfolio=portfolio,
        sources={k: p.name for k, p in files.items()},
    )


def boutiques_available(
    stock: Iterable[StockSnapshotLine], dc_mapping: dict[str, str]
) -> list[str]:
    """Boutiques présentes à la fois dans DC Mapping et dans la Stock Situation."""
    locations = {line.location for line in stock}
    return sorted(b for b in dc_mapping if b in locations)


def build_inputs_for_boutique(
    boutique: str,
    stock: Iterable[StockSnapshotLine],
    movements: Iterable[StockMovement],
    products: dict[str, Product],
    conversions: Iterable[SkuConversion],
    dc_mapping: dict[str, str],
    *,
    included_movement_codes: Collection[str] | None = None,
    sources: dict[str, str] | None = None,
) -> tuple[ForecastInputs, str]:
    """Assemble les entrées du moteur pour une boutique. Retourne (entrées, DC)."""
    dc = dc_mapping.get(boutique)
    if dc is None:
        raise SchemaError(f"Boutique {boutique} absente de DC Mapping")
    stock = list(stock)
    boutique_stock = {x.sku: x for x in stock if x.location == boutique}
    dc_stock = {x.sku: x for x in stock if x.location == dc}
    if not boutique_stock:
        raise SchemaError(f"Aucune ligne de Stock Situation pour la boutique {boutique}")
    if not dc_stock:
        raise SchemaError(f"Aucune ligne de Stock Situation pour le DC {dc}")
    btq_moves = [m for m in movements if m.location == boutique]
    if not btq_moves:
        raise SchemaError(f"Aucun mouvement de stock pour la boutique {boutique}")
    return (
        ForecastInputs(
            boutique_stock=boutique_stock,
            dc_stock=dc_stock,
            sales=aggregate_sales(btq_moves, included_movement_codes),
            products=products,
            conversions=build_conversion_index(conversions),
            sources=sources or {},
        ),
        dc,
    )
