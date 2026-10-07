"""Tests de l'agrégation des ventes et du chargement des fixtures."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.domain.models import StockMovement
from app.engines.sales import aggregate_sales
from app.ingestion.fixtures_loader import SchemaError, load_fixture_dir, load_multiples

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "b80_2026-10-05"


def mv(sku: str, qty: float, day: int, code: str) -> StockMovement:
    return StockMovement(
        movement_id=f"{sku}{day}{code}", location="B80", sku=sku,
        movement_date=date(2026, 9, day), movement_code=code, quantity=qty,
    )  # fmt: skip


MOVES = [
    mv("7005.70", 100, 28, "8730"),
    mv("7005.70", 20, 29, "9044"),  # Recycling Program : compté par défaut
    mv("0005452", 3, 29, "8730"),
    mv("7005.70", 5, 30, "8751"),
]


def test_all_movement_codes_by_default() -> None:
    s = aggregate_sales(MOVES)
    assert s.sales("7005.70") == 125
    assert s.sales("0005452") == 3
    assert s.sales("inconnu") == 0
    assert s.history_days == 3
    assert s.movement_codes is None


def test_movement_codes_filter_keeps_history_days() -> None:
    s = aggregate_sales(MOVES, included_movement_codes=["8730"])
    assert s.sales("7005.70") == 100
    assert s.history_days == 3


def test_fixture_skus_stay_text() -> None:
    data = load_fixture_dir(FIXTURE_DIR)
    skus = {line.sku for line in data.stock_situation}
    assert "7005.70" in skus
    assert all(isinstance(s, str) for s in skus)
    leading_zero = [s for s in data.products if s.startswith("0") and len(s) > 1]
    assert leading_zero, "les zéros initiaux doivent être conservés"


def test_multiples_first_clean_row_wins() -> None:
    products, anomalies = load_multiples(FIXTURE_DIR / "master_multiples.csv")
    assert products["7922.70"].order_multiple == 800  # première ligne, comme RECHERCHEV
    assert "7922.70" in anomalies
    # « 473ECO/B » existe avec et sans espace devant : la ligne propre gagne.
    assert products["473ECO/B"].units_per_pallet == 50.0
    assert any("473ECO/B" in a and "espaces" in a for a in anomalies)


def test_wrong_columns_raise(tmp_path: Path) -> None:
    bad = tmp_path / "master_multiples.csv"
    bad.write_text("sku,multiple\n7005.70,240\n", encoding="utf-8")
    with pytest.raises(SchemaError):
        load_multiples(bad)


def test_empty_numeric_value_raises(tmp_path: Path) -> None:
    from app.ingestion.fixtures_loader import load_stock_situation

    f = tmp_path / "ss.csv"
    f.write_text(
        "Stock,Product,Product Descr,Product Type,Expected,Available,Incoming,Reserved,Waiting\n"
        "B80,0005452,X,C,,1,0,0,0\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError):
        load_stock_situation(f)
