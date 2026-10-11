"""Chargement des fixtures CSV de test (tests/fixtures/<cas>/).

Toutes les colonnes sont lues en texte (`dtype=str`) : les SKU ne sont JAMAIS
convertis en nombre. Les colonnes numériques sont converties explicitement, et
une valeur illisible lève une erreur au lieu d'être remplacée par 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.domain.models import Product, SkuConversion, StockMovement, StockSnapshotLine
from app.engines.conversion import build_conversion_index
from app.engines.forecast import ForecastInputs
from app.engines.portfolio import PortfolioIndex
from app.engines.sales import aggregate_sales, fallback_history

if TYPE_CHECKING:  # pandas n'est chargé que pour lire des CSV (absent de la version web)
    import pandas as pd


class SchemaError(ValueError):
    """Le fichier n'a pas la structure attendue."""


STOCK_SITUATION_COLUMNS = [
    "Stock", "Product", "Product Descr", "Product Type",
    "Expected", "Available", "Incoming", "Reserved", "Waiting",
]  # fmt: skip
MOVEMENT_COLUMNS = [
    "movement_id", "location", "sku", "product_type", "movement_date",
    "movement_code", "quantity", "movement_description",
]  # fmt: skip
MULTIPLE_COLUMNS = ["sku", "description", "type", "order_multiple", "units_per_pallet"]
CONVERSION_COLUMNS = ["old_sku", "new_sku", "effective_date", "comment"]
DC_MAPPING_COLUMNS = ["boutique", "dc"]
PORTFOLIO_COLUMNS = ["boutique", "sku", "filter_out"]


@dataclass(frozen=True)
class FixtureData:
    parameters: dict[str, Any]
    stock_situation: list[StockSnapshotLine]
    movements: list[StockMovement]
    products: dict[str, Product]
    product_list_anomalies: list[str]
    conversions: list[SkuConversion]
    dc_mapping: dict[str, str]
    portfolio: dict[str, str]
    sources: dict[str, str]


def read_csv_as_text(path: Path, expected_columns: list[str]) -> pd.DataFrame:
    """Lit un CSV entièrement en texte et vérifie les colonnes (ordre compris)."""
    import pandas as pd

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != expected_columns:
        raise SchemaError(
            f"{path.name} : colonnes {list(df.columns)} au lieu de {expected_columns}"
        )
    return df.map(lambda v: v.strip())


def _opt(value: str) -> str | None:
    return value or None


def _num(value: str, column: str, path: Path) -> float:
    if value == "":
        raise SchemaError(f"{path.name} : valeur vide dans la colonne numérique {column!r}")
    try:
        return float(value)
    except ValueError as exc:
        raise SchemaError(f"{path.name} : {value!r} n'est pas un nombre ({column})") from exc


def _int_multiple(value: str, path: Path) -> int | None:
    if value == "":
        return None
    number = _num(value, "order_multiple", path)
    if not number.is_integer():
        raise SchemaError(f"{path.name} : multiple non entier {value!r}")
    return int(number)


def load_stock_situation(path: Path) -> list[StockSnapshotLine]:
    df = read_csv_as_text(path, STOCK_SITUATION_COLUMNS)
    return [
        StockSnapshotLine(
            location=r["Stock"],
            sku=r["Product"],
            description=_opt(r["Product Descr"]),
            product_type=_opt(r["Product Type"]),
            expected=_num(r["Expected"], "Expected", path),
            available=_num(r["Available"], "Available", path),
            incoming=_num(r["Incoming"], "Incoming", path),
            reserved=_num(r["Reserved"], "Reserved", path),
            waiting=_num(r["Waiting"], "Waiting", path),
        )
        for r in df.to_dict("records")
    ]


def load_movements(path: Path) -> list[StockMovement]:
    df = read_csv_as_text(path, MOVEMENT_COLUMNS)
    return [
        StockMovement(
            movement_id=r["movement_id"],
            location=r["location"],
            sku=r["sku"],
            product_type=_opt(r["product_type"]),
            movement_date=date.fromisoformat(r["movement_date"]),
            movement_code=r["movement_code"],
            quantity=_num(r["quantity"], "quantity", path),
            movement_description=_opt(r["movement_description"]),
        )
        for r in df.to_dict("records")
    ]


def load_multiples(path: Path) -> tuple[dict[str, Product], list[str]]:
    """Multiple list. En cas de doublon, la PREMIÈRE ligne gagne (comme RECHERCHEV).

    Un SKU entouré d'espaces (« ICE-179  ») n'est jamais trouvé par RECHERCHEV :
    une ligne propre du même SKU a donc toujours priorité sur une ligne avec espaces.
    Retourne aussi la liste des SKU en double ou mal formés, pour qu'ils restent visibles.
    """
    import pandas as pd

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != MULTIPLE_COLUMNS:
        raise SchemaError(
            f"{path.name} : colonnes {list(df.columns)} au lieu de {MULTIPLE_COLUMNS}"
        )
    records = df.to_dict("records")
    clean = [r for r in records if r["sku"] == r["sku"].strip()]
    padded = [r for r in records if r["sku"] != r["sku"].strip()]
    products: dict[str, Product] = {}
    anomalies: list[str] = [f"{r['sku']!r} (espaces autour du SKU)" for r in padded]
    for r in clean + padded:
        sku = r["sku"].strip()
        if sku in products:
            anomalies.append(sku)
            other = _int_multiple(r["order_multiple"].strip(), path)
            first = products[sku]
            if other is not None and other != first.order_multiple and r in clean:
                alts = tuple(sorted({*first.alt_multiples, other}))
                products[sku] = first.model_copy(update={"alt_multiples": alts})
            continue
        pallet = r["units_per_pallet"].strip()
        products[sku] = Product(
            sku=sku,
            description=_opt(r["description"].strip()),
            product_type=_opt(r["type"].strip()),
            order_multiple=_int_multiple(r["order_multiple"].strip(), path),
            units_per_pallet=_num(pallet, "units_per_pallet", path) if pallet else None,
        )
    return products, anomalies


def load_conversions(path: Path) -> list[SkuConversion]:
    df = read_csv_as_text(path, CONVERSION_COLUMNS)
    return [
        SkuConversion(
            old_sku=r["old_sku"],
            new_sku=r["new_sku"],
            effective_date=date.fromisoformat(r["effective_date"]),
            comment=_opt(r["comment"]),
        )
        for r in df.to_dict("records")
    ]


def load_fixture_dir(directory: Path) -> FixtureData:
    """Charge un dossier de fixture complet (structure de tests/fixtures/b80_2026-10-05)."""
    params = json.loads((directory / "parameters.json").read_text(encoding="utf-8"))
    boutique = params["boutique"]
    files = {
        "stock_situation": next(directory.glob(f"stock_situation_{boutique}_*.csv")),
        "movements": directory / f"stock_movements_{boutique}.csv",
        "multiples": directory / "master_multiples.csv",
        "conversions": directory / "master_sku_conversions.csv",
        "dc_mapping": directory / "master_dc_mapping.csv",
        "portfolio": directory / f"master_boutique_portfolio_{boutique}.csv",
    }
    products, duplicates = load_multiples(files["multiples"])
    dc_df = read_csv_as_text(files["dc_mapping"], DC_MAPPING_COLUMNS)
    pf_df = read_csv_as_text(files["portfolio"], PORTFOLIO_COLUMNS)
    return FixtureData(
        parameters=params,
        stock_situation=load_stock_situation(files["stock_situation"]),
        movements=load_movements(files["movements"]),
        products=products,
        product_list_anomalies=duplicates,
        conversions=load_conversions(files["conversions"]),
        dc_mapping=dict(zip(dc_df["boutique"], dc_df["dc"], strict=True)),
        portfolio=dict(zip(pf_df["sku"], pf_df["filter_out"], strict=True)),
        sources={name: path.name for name, path in files.items()},
    )


def build_forecast_inputs(
    data: FixtureData,
    included_movement_codes: list[str] | None = None,
) -> ForecastInputs:
    """Assemble les entrées du moteur à partir d'une fixture chargée."""
    boutique = data.parameters["boutique"]
    dc = data.dc_mapping.get(boutique)
    if dc is None:
        raise SchemaError(f"Boutique {boutique} absente de DC Mapping")
    boutique_stock = {line.sku: line for line in data.stock_situation if line.location == boutique}
    dc_stock = {line.sku: line for line in data.stock_situation if line.location == dc}
    if not dc_stock:
        raise SchemaError(f"Aucune ligne de stock pour le DC {dc} de la boutique {boutique}")
    moves = [mv for mv in data.movements if mv.location == boutique]
    return ForecastInputs(
        boutique_stock=boutique_stock,
        dc_stock=dc_stock,
        sales=aggregate_sales(moves, included_movement_codes),
        products=data.products,
        conversions=build_conversion_index(data.conversions),
        sources=data.sources,
        portfolio=PortfolioIndex.build(data.portfolio),
        fallback=fallback_history(moves, 4, included_movement_codes),
    )
