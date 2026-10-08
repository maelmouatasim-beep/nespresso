"""Tests des décisions du planner (étape 2) : A3 multiples, A4 portfolio numérique,
A5 périmètre, A6 retour en stock DC, A7 historique de secours, A8 exclusions,
A9 multiples en double."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.domain.models import (
    Exclusion,
    ForecastMode,
    Override,
    PlanningParameters,
    Product,
    QtySource,
    Status,
    StockMovement,
)
from app.engines.conversion import build_conversion_index
from app.engines.forecast import (
    REASON_DC_OUT,
    REASON_DC_RETURN,
    REASON_INFERRED_MULTIPLE,
    REASON_MISSING_MULTIPLE,
    REASON_NO_ACTIVITY,
    REASON_NO_FALLBACK,
    REASON_SUSPECT_MULTIPLE,
    REASON_TWO_MULTIPLES,
    ForecastInputs,
    compute_line,
)
from app.engines.overrides import apply_overrides
from app.engines.portfolio import PortfolioIndex, numeric_key
from app.engines.sales import aggregate_sales, fallback_history
from app.engines.selection import select_active_skus, select_order_skus
from app.ingestion.fixtures_loader import build_forecast_inputs, load_fixture_dir, load_multiples
from tests.unit.test_forecast import simple, snap

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "b80_2026-10-05"
RUN = date(2026, 10, 5)
STD, XL = ForecastMode.STANDARD, ForecastMode.EXCEL_PARITY


def params(cover: float = 7) -> PlanningParameters:
    return PlanningParameters(boutique="B80", run_date=RUN, delivery_date=RUN,
                              cover_days=cover, coffee_cover_days=None)  # fmt: skip


def mv(sku: str, qty: float, day: date) -> StockMovement:
    return StockMovement(movement_id=f"{sku}{day}", location="B80", sku=sku,
                         movement_date=day, movement_code="8730", quantity=qty)  # fmt: skip


# --- A3 : arrondi au multiple partout, jamais 1 en silence ---------------------------------


@pytest.mark.parametrize(("desc", "expected"), [("VER-Melozio", 240), ("ORI-Cosi", 800)])
def test_a3_coffee_without_multiple_gets_family_multiple(desc: str, expected: int) -> None:
    line = simple(sales=10, expected=0, multiple=None, desc=desc, mode=STD)
    assert line.explanation["multiple"] == expected and line.qty == expected
    assert line.status is Status.REVIEW
    assert any(r.startswith(REASON_INFERRED_MULTIPLE) for r in line.reasons)


def test_a3_non_coffee_without_multiple_is_review_missing() -> None:
    line = simple(sales=10, expected=0, multiple=None, ptype="LC", desc="Mug", mode=STD)
    assert line.status is Status.REVIEW and not line.qty_reliable
    assert any(r.startswith(REASON_MISSING_MULTIPLE) for r in line.reasons)


def test_a3_coffee_with_multiple_one_is_suspect() -> None:
    line = simple(sales=10, expected=0, multiple=1, mode=STD)
    assert any(r.startswith(REASON_SUSPECT_MULTIPLE) for r in line.reasons)


def test_a3_override_is_rounded_up_to_multiple() -> None:
    base = simple(sales=8350, expected=7800, p=params(9), mode=STD)  # 3120, multiple 240
    ov = Override(sku="7005.70", qty_before=3120, qty_after=3500, category="promo",
                  reason="Promo AOS", author="A", timestamp=datetime(2026, 10, 5))  # fmt: skip
    out = apply_overrides([base], [ov])[0]
    assert out.qty == 3600
    assert out.explanation["override"]["qty_final"] == 3600
    assert any("arrondie au multiple de 240" in r for r in out.reasons)


# --- A4 : portfolio comparé en numérique puis en texte -----------------------------------------


@pytest.mark.parametrize(
    ("sku", "key"),
    [("7005.70", "7005.7"), ("7005.7", "7005.7"), ("0005452", "5452"), ("3517/BULK", None),
     ("127708/CA", None), ("J620-US-ME-BV", None), ("", None)],
)  # fmt: skip
def test_a4_numeric_key(sku: str, key: str | None) -> None:
    assert numeric_key(sku) == key


def test_a4_portfolio_matches_numeric_then_text() -> None:
    idx = PortfolioIndex.build({"7005.7": "No", "3517/BULK": "Yes"})
    assert idx.flag("7005.70") == "No"
    assert idx.filtered_out("3517/BULK")
    assert idx.flag("7006.70") is None


def test_a4_b80_selection_matches_excel_451_lines() -> None:
    data = load_fixture_dir(FIXTURE)
    inputs = build_forecast_inputs(data)
    golden = pd.read_csv(FIXTURE / "golden_stock_cover_final_B80.csv", dtype=str)
    excel_rows = set(golden["sku"][:451])  # les 10 dernières lignes sont des ajouts manuels
    sel = select_order_skus(inputs.boutique_stock, inputs.sales, inputs.portfolio,
                            inputs.conversions)  # fmt: skip
    assert excel_rows <= set(sel.skus)  # les 451 lignes d'Excel sont retrouvées
    # Seul écart restant : 2007.70 (40 ventes, hors portfolio), absent d'Excel (Q-002).
    assert set(sel.skus) - excel_rows == {"2007.70"}
    assert len(sel.skus) == 452


# --- A5 : périmètre = tout SKU avec activité ---------------------------------------------------


def test_a5_scope_includes_every_active_sku() -> None:
    stock = {
        "PORT": snap("PORT", 0),  # dans le portfolio seulement
        "AVAIL": snap("AVAIL", 0).model_copy(update={"available": 3}),  # Available non nul
        "INC": snap("INC", 0).model_copy(update={"incoming": 5}),  # Incoming non nul
        "DEAD": snap("DEAD", 0),  # aucune activité
    }
    sales = aggregate_sales([mv("MOVE", 2, RUN)])
    portfolio = PortfolioIndex.build({"PORT": "No", "ONLYPF": "No"})
    sel = select_active_skus(stock, sales, portfolio, known_skus=[])
    assert {"PORT", "AVAIL", "INC", "MOVE", "ONLYPF"} <= set(sel.skus)
    assert "DEAD" not in sel.skus and "DEAD" in sel.excluded


def test_a5_portfolio_yes_is_shown_blocked_in_standard() -> None:
    inputs = _dormant_inputs(dc=100, portfolio={"7005.70": "Yes"})
    line = compute_line("7005.70", inputs, params(), STD)
    assert line.qty == 0 and line.status is Status.BLOCKED
    assert any("Filter out = Yes" in r for r in line.reasons)


# --- A6 / A7 : SKU dormant, retour en stock DC, historique de secours ----------------------------


def _dormant_inputs(*, dc: float | None, portfolio=None, history=()) -> ForecastInputs:
    moves = list(history) or [mv("OTHER", 1, RUN - timedelta(days=i)) for i in range(7)]
    stock = {"7005.70": snap("7005.70", 0, desc="VER-Melozio")}
    return ForecastInputs(
        boutique_stock=stock,
        dc_stock={} if dc is None else {"7005.70": snap("7005.70", dc, loc="CY1")},
        sales=aggregate_sales(moves, window_days=7),
        products={"7005.70": Product(sku="7005.70", product_type="C", order_multiple=240)},
        conversions=build_conversion_index([]),
        portfolio=PortfolioIndex.build(portfolio if portfolio is not None else {"7005.7": "No"}),
        fallback=fallback_history(moves, weeks=4),
    )


def _history_with_old_sales() -> list[StockMovement]:
    # 6 semaines : ventes dans les semaines 3, 4 et 5 (avant la rupture), rien ensuite.
    moves = [mv("OTHER", 1, RUN - timedelta(days=i)) for i in range(42)]
    for week, units in ((2, 70), (3, 140), (4, 210)):
        moves.append(mv("7005.70", units, RUN - timedelta(days=7 * week + 1)))
    return moves


def test_a7_fallback_history_uses_selling_weeks_only() -> None:
    fb = fallback_history(_history_with_old_sales(), weeks=4)
    rate = fb.rate("7005.70")
    assert fb.full_weeks == 6
    assert rate is not None and rate.weeks_used == 3
    assert float(rate.daily_rate) == pytest.approx(420 / 21)  # 420 unités / 3 semaines


def test_a7_window_keeps_last_days_only() -> None:
    s = aggregate_sales(_history_with_old_sales(), window_days=7)
    assert s.history_days == 7 and s.sales("7005.70") == 0


def test_a6_dormant_back_in_dc_gets_suggested_qty() -> None:
    inputs = _dormant_inputs(dc=5000, history=_history_with_old_sales())
    line = compute_line("7005.70", inputs, params(cover=9), STD)
    # 9 j × 20/j = 180 → arrondi au multiple 240
    assert line.qty == 240 and line.source is QtySource.DC_RETURN
    assert line.status is Status.REVIEW and REASON_DC_RETURN in line.reasons
    assert line.explanation["fallback"]["weeks_used"] == 3


def test_a6_parity_mode_flags_but_keeps_excel_zero() -> None:
    inputs = _dormant_inputs(dc=5000, history=_history_with_old_sales())
    line = compute_line("7005.70", inputs, params(cover=9), XL)
    assert line.qty == 0 and line.status is Status.REVIEW
    assert any("non appliquée" in r for r in line.reasons)


def test_a6_dormant_without_fallback_is_review_never_silent() -> None:
    line = compute_line("7005.70", _dormant_inputs(dc=5000), params(), STD)
    assert line.qty == 0 and line.status is Status.REVIEW
    assert REASON_DC_RETURN in line.reasons and REASON_NO_FALLBACK in line.reasons


def test_a6_dc_out_of_stock_is_ok_with_reason() -> None:
    line = compute_line("7005.70", _dormant_inputs(dc=0), params(), STD)
    assert line.qty == 0 and line.status is Status.OK and REASON_DC_OUT in line.reasons


def test_a6_not_relevant_sku_stays_no_activity() -> None:
    inputs = _dormant_inputs(dc=5000, portfolio={})
    line = compute_line("7005.70", inputs, params(), STD)
    assert line.status is Status.OK and REASON_NO_ACTIVITY in line.reasons


def test_a6_b80_has_dormant_lines_in_review() -> None:
    data = load_fixture_dir(FIXTURE)
    inputs = build_forecast_inputs(data)
    line = compute_line("114368", inputs, params(cover=9), STD)  # sacs : portfolio, DC > 0
    assert REASON_DC_RETURN in line.reasons and line.status is Status.REVIEW


# --- A8 : exclusions éditables -------------------------------------------------------------------


def _excl(boutiques: tuple[str, ...] = ()) -> Exclusion:
    return Exclusion(sku="7010.7", boutiques=boutiques, reason="SKU e-commerce, à vérifier",
                     author="Planner A", updated_on=date(2026, 10, 7))  # fmt: skip


@pytest.mark.parametrize("mode", [STD, XL])
def test_a8_exclusion_blocks_with_visible_reason(mode: ForecastMode) -> None:
    inputs = _dormant_inputs(dc=10, history=[mv("7010.70", 700, RUN)])
    inputs = ForecastInputs(**{**inputs.__dict__, "exclusions": (_excl(),),
                               "boutique_stock": {"7010.70": snap("7010.70", 0)}})  # fmt: skip
    line = compute_line("7010.70", inputs, params(), mode)
    assert line.qty == 0 and line.status is Status.BLOCKED
    assert line.source is QtySource.EXCLUSION
    assert any(r.startswith("Exclusion : SKU e-commerce") for r in line.reasons)
    assert line.explanation["exclusion"]["author"] == "Planner A"


def test_a8_exclusion_limited_to_listed_boutiques() -> None:
    assert _excl(("B1",)).applies_to("B1") and not _excl(("B1",)).applies_to("B80")
    assert _excl().applies_to("B80")


# --- A9 : deux multiples différents pour un SKU ----------------------------------------------


def test_a9_loader_keeps_both_multiples_for_7922() -> None:
    products, _ = load_multiples(FIXTURE / "master_multiples.csv")
    assert products["7922.70"].order_multiple == 800
    assert products["7922.70"].alt_multiples == (240,)


def test_a9_engine_picks_family_multiple_and_warns() -> None:
    inputs = _dormant_inputs(dc=10_000, history=[mv("7922.70", 700, RUN)])
    inputs = ForecastInputs(**{
        **inputs.__dict__,
        "boutique_stock": {"7922.70": snap("7922.70", 0, desc="ORI-Vaniglia Decaf")},
        "products": {"7922.70": Product(sku="7922.70", product_type="C", order_multiple=240,
                                        alt_multiples=(800,))},
    })  # fmt: skip
    line = compute_line("7922.70", inputs, params(), STD)
    assert line.explanation["multiple"] == 800
    assert any(r.startswith(REASON_TWO_MULTIPLES) for r in line.reasons)
