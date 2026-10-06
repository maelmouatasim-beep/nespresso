"""Tests : arbitrage métier, overrides, sélection, palettes, export, résumé."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.domain.models import (
    Allocation,
    ForecastMode,
    Launch,
    Override,
    PlanningParameters,
    Product,
    QtySource,
    RecommendationLine,
    Status,
    TargetStock,
)
from app.engines.anomalies import summarize
from app.engines.business import BusinessInputError, allocation_due, apply_business_rules
from app.engines.compare import compare_orders
from app.engines.conversion import build_conversion_index
from app.engines.export import lt_content, lt_filename, lt_lines, recap_csv
from app.engines.overrides import OverrideError, apply_overrides
from app.engines.pallets import MACHINES, OL, OTHERS, VL, estimate_pallets
from app.engines.sales import SalesSummary
from app.engines.selection import select_order_skus
from tests.unit.test_forecast import make_inputs, snap

RUN = date(2026, 10, 5)


def line(
    sku: str,
    qty: int,
    *,
    expected: float = 0,
    multiple: int = 240,
    status: Status = Status.OK,
    ptype: str = "C",
    desc: str = "VER-X",
) -> RecommendationLine:
    return RecommendationLine(
        sku=sku, description=desc, product_type=ptype, qty=qty, qty_reliable=True,
        status=status, reasons=[],
        explanation={"inputs": {"expected_total": expected}, "multiple": multiple},
    )  # fmt: skip


def alloc(**kw) -> Allocation:
    base = dict(boutique="B80", sku="7005.70", total_qty=1000, already_sent=0,
                wave_plan=(75, 25), current_wave=1)  # fmt: skip
    return Allocation(**{**base, **kw})


# --- Allocations -------------------------------------------------------------------


def test_allocation_due_waves() -> None:
    assert allocation_due(alloc()) == (750, 75)
    assert allocation_due(alloc(current_wave=2, already_sent=750)) == (250, 100)
    assert allocation_due(alloc(current_wave=1, already_sent=800)) == (0, 75)  # jamais négatif


def test_allocation_wave_plan_must_total_100() -> None:
    with pytest.raises(ValidationError):
        alloc(wave_plan=(50, 25))
    with pytest.raises(ValidationError):
        alloc(current_wave=3)


def test_allocation_replaces_forecast_never_added() -> None:
    out = apply_business_rules([line("7005.70", 3120)], boutique="B80", run_date=RUN,
                               allocations=[alloc(total_qty=960, wave_plan=(100,))])  # fmt: skip
    assert out[0].qty == 960
    assert out[0].forecast_qty == 3120
    assert out[0].source is QtySource.ALLOCATION


def test_allocation_wins_over_launch_and_flags_non_multiple() -> None:
    launch = Launch(boutique="B80", sku="7005.70", launch_date=date(2026, 10, 20), qty=5000)
    out = apply_business_rules([line("7005.70", 0)], boutique="B80", run_date=RUN,
                               allocations=[alloc(total_qty=1000, wave_plan=(100,))],
                               launches=[launch])  # fmt: skip
    assert out[0].qty == 1000
    assert out[0].status is Status.REVIEW  # 1000 n'est pas multiple de 240
    assert any("Lancement ignoré" in r for r in out[0].reasons)


def test_allocation_on_blocked_line_is_review() -> None:
    out = apply_business_rules([line("7039.70", 0, status=Status.BLOCKED)], boutique="B80",
                               run_date=RUN, allocations=[alloc(sku="7039.70", total_qty=240,
                               wave_plan=(100,))])  # fmt: skip
    assert out[0].qty == 240
    assert out[0].status is Status.REVIEW


def test_other_boutique_allocations_ignored_and_duplicates_refused() -> None:
    out = apply_business_rules([line("7005.70", 10)], boutique="B80", run_date=RUN,
                               allocations=[alloc(boutique="B1")])  # fmt: skip
    assert out[0].qty == 10
    with pytest.raises(BusinessInputError):
        apply_business_rules([line("7005.70", 10)], boutique="B80", run_date=RUN,
                             allocations=[alloc(), alloc()])  # fmt: skip


# --- Lancements et stock cible -------------------------------------------------------


def test_launch_before_date_subtracts_expected() -> None:
    launch = Launch(boutique="B80", sku="7200.70", launch_date=date(2026, 10, 14), qty=3600)
    out = apply_business_rules([line("7200.70", 0, expected=500)], boutique="B80",
                               run_date=RUN, launches=[launch])  # fmt: skip
    assert out[0].qty == 3120  # 3600 − 500 = 3100 → 3120 (multiple 240)
    assert out[0].source is QtySource.LAUNCH
    assert out[0].status is Status.REVIEW


def test_launch_after_date_keeps_forecast() -> None:
    launch = Launch(boutique="B80", sku="7200.70", launch_date=date(2026, 9, 1), qty=3600)
    out = apply_business_rules([line("7200.70", 480)], boutique="B80", run_date=RUN,
                               launches=[launch])  # fmt: skip
    assert out[0].qty == 480
    assert out[0].source is QtySource.FORECAST


def test_target_stock_raises_to_target_and_caps_at_max() -> None:
    t = TargetStock(boutique="B80", sku="A", min_qty=100, target_qty=200, max_qty=300)
    low = apply_business_rules([line("A", 0, expected=50, multiple=10)], boutique="B80",
                               run_date=RUN, targets=[t])  # fmt: skip
    assert low[0].qty == 150 and low[0].source is QtySource.TARGET_STOCK
    t2 = TargetStock(boutique="B80", sku="B", min_qty=0, target_qty=100, max_qty=300)
    high = apply_business_rules([line("B", 480, expected=100, multiple=240)], boutique="B80",
                                run_date=RUN, targets=[t2])  # fmt: skip
    assert high[0].qty == 0  # place restante 200 < 1 multiple de 240


def test_target_stock_ignored_for_blocked() -> None:
    t = TargetStock(boutique="B80", sku="A", min_qty=100, target_qty=200, max_qty=300)
    out = apply_business_rules([line("A", 0, status=Status.BLOCKED)], boutique="B80",
                               run_date=RUN, targets=[t])  # fmt: skip
    assert out[0].qty == 0


def test_target_stock_order_validated() -> None:
    with pytest.raises(ValidationError):
        TargetStock(boutique="B80", sku="A", min_qty=300, target_qty=200, max_qty=100)


# --- Overrides ------------------------------------------------------------------------


def ov(**kw) -> Override:
    base = dict(sku="7005.70", qty_before=3120, qty_after=3600, category="promo",
                reason="Promo AOS 14-21 oct",
                author="Planner A", timestamp=datetime(2026, 10, 5, 11, 51))  # fmt: skip
    return Override(**{**base, **kw})


def test_override_requires_reason_and_author() -> None:
    with pytest.raises(ValidationError):
        ov(reason="  ")
    with pytest.raises(ValidationError):
        ov(author="")


def test_override_applied_with_trace() -> None:
    out = apply_overrides([line("7005.70", 3120)], [ov()])
    assert out[0].qty == 3600
    assert out[0].forecast_qty == 3120
    assert out[0].source is QtySource.OVERRIDE
    assert out[0].explanation["override"]["author"] == "Planner A"


def test_override_must_match_current_qty() -> None:
    with pytest.raises(OverrideError):
        apply_overrides([line("7005.70", 2880)], [ov()])
    with pytest.raises(OverrideError):
        apply_overrides([line("X", 1)], [ov()])


# --- Sélection ---------------------------------------------------------------------------


def test_selection_filter_out_rule() -> None:
    stock = {s.sku: s for s in [snap("A", 0), snap("B", 0), snap("C", 5), snap("D", 0)]}
    sales = SalesSummary(by_sku={"E": 3}, history_days=7, movement_codes=None)
    res = select_order_skus(
        stock, sales, portfolio={"A": "Yes", "B": "No"},
        conversions=build_conversion_index([]), forced_skus=["D"],
    )  # fmt: skip
    assert res.skus == ["B", "C", "D", "E"]
    assert res.excluded == {"A": "Boutique Portfolio : Filter out = Yes"}


def test_selection_excludes_inactive_out_of_portfolio() -> None:
    stock = {"Z": snap("Z", 0)}
    sales = SalesSummary(by_sku={}, history_days=7, movement_codes=None)
    res = select_order_skus(stock, sales, {}, build_conversion_index([]))
    assert res.skus == [] and "Z" in res.excluded


# --- Palettes, export, résumé -------------------------------------------------------------


def test_pallets_by_family_and_cy1() -> None:
    lines = [
        line("V", 8640, desc="VER-Melozio"),
        line("O", 28800, desc="ORI-Cosi"),
        line("M", 24, ptype="M", desc="Machine"),
        line("L", 10, ptype="LC", desc="Mug"),
        line("N", 5, ptype="LC", desc="Sans palette"),
    ]
    products = {"L": Product(sku="L", units_per_pallet=20)}
    est = estimate_pallets(lines, products, "CY1")
    assert est.pallets[VL] == 1 and est.pallets[OL] == 1 and est.pallets[MACHINES] == 1
    assert est.pallets[OTHERS] == 0.5
    assert est.skus_without_pallet_size == ["N"]
    assert estimate_pallets(lines, products, "MT1").pallets[VL] == 8640 / 8000


def test_lt_export_format() -> None:
    lines = [line("0005452", 12), line("7005.70", 0), line("3517/BULK", 3)]
    assert lt_lines(lines) == ["0005452;12", "3517/BULK;3"]
    assert lt_content(lines) == "0005452;12\r\n3517/BULK;3\r\n"
    assert lt_filename("B80", datetime(2026, 10, 5, 11, 51)) == "B80 05-Oct-2026-11_51.csv"


def test_recap_contains_pallets() -> None:
    lines = [line("V", 8000, desc="VER-Melozio")]
    est = estimate_pallets(lines, {}, "MT1")
    text = recap_csv("B80", "2026-10-07", lines, est)
    assert "V;VER-Melozio;C;Cafés Vertuo (VL);8000;forecast" in text
    assert "Total;8000;1.00" in text


def test_summary_counts() -> None:
    s = summarize([line("A", 240), line("B", 0, status=Status.REVIEW)])
    assert (s.lines_total, s.lines_ordered, s.units_total) == (2, 1, 240)
    assert s.by_status == {"OK": 1, "REVIEW": 1, "BLOCKED": 0}


def test_compare_orders() -> None:
    assert compare_orders({"A": 240, "B": 10}, {"A": 480, "C": 5}) == [
        {"sku": "A", "avant": 240, "maintenant": 480, "écart": 240},
        {"sku": "B", "avant": 10, "maintenant": 0, "écart": -10},
        {"sku": "C", "avant": 0, "maintenant": 5, "écart": 5},
    ]


# --- Paramètres -------------------------------------------------------------------------


def test_delivery_date_cannot_precede_run_date() -> None:
    with pytest.raises(ValidationError):
        PlanningParameters(boutique="B80", run_date=RUN, delivery_date=date(2026, 10, 4),
                           cover_days=9, coffee_cover_days=None)  # fmt: skip


def test_projected_stock_at_delivery_is_display_only() -> None:
    from app.engines.forecast import compute_line

    inputs = make_inputs(
        stock=[snap("A", 700)],
        sales={"A": 700},
        products=[Product(sku="A", product_type="C", order_multiple=10)],
    )
    p = PlanningParameters(boutique="B80", run_date=RUN, delivery_date=date(2026, 10, 8),
                           cover_days=9, coffee_cover_days=None)  # fmt: skip
    res = compute_line("A", inputs, p, ForecastMode.EXCEL_PARITY)
    assert res.explanation["days_until_delivery"] == 3
    assert res.explanation["projected_stock_at_delivery"] == 400  # 700 − 100 × 3
    assert res.qty == 200  # 9 × 100 − 700 : la date de livraison ne change pas la qty
