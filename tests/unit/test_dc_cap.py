"""D-051 : plafonnement de la Qty proposée au stock DC (mode Safe), parité Excel intacte."""

from __future__ import annotations

from datetime import date, datetime
from fractions import Fraction

import pytest

from app.domain.models import (
    ForecastMode,
    Launch,
    Override,
    Product,
    QtySource,
    Status,
)
from app.engines.dc_cap import REASON_CAPPED, REASON_OVER, cap_line, floor_to_multiple, over_dc
from app.engines.overrides import apply_overrides
from app.services.planning import BusinessInputs, run_planning
from tests.unit.test_forecast import EXCEL, make_inputs, params, simple, snap

SAFE = ForecastMode.STANDARD


@pytest.mark.parametrize(
    ("value", "multiple", "expected"),
    [(500, 240, 480), (0, 240, 0), (239, 240, 0), (480, 240, 480), (1200, 800, 800),
     (Fraction(7, 2), 1, 3), (10, 0, 10), (-5, 240, 0)],
)  # fmt: skip
def test_floor_to_multiple(value, multiple, expected) -> None:  # noqa: ANN001
    assert floor_to_multiple(value, multiple) == expected


def test_safe_mode_caps_to_dc_and_keeps_ok() -> None:
    # besoin 7 × 400 ÷ 7 = 400 − 0 → 480 (multiple 240) ; DC 500 → 480 ; DC 0 → 0
    line = simple(sales=400, expected=0, dc=500, mode=SAFE)
    assert (line.qty, line.status) == (480, Status.OK)
    assert not any(REASON_CAPPED in r for r in line.reasons)
    high = cap_line(simple(sales=1050, expected=0, dc=500, mode=SAFE), SAFE)
    assert (high.qty, high.status, high.forecast_qty) == (480, Status.OK, 1200)
    assert high.reasons[-1] == f"{REASON_CAPPED} (besoin 1 200, DC 500 → 480)"
    assert high.explanation["dc_cap"] == {"need": 1200, "dc": 500.0, "qty": 480, "multiple": 240,
                                          "rule_version": "dc_cap-1.0.0"}  # fmt: skip
    assert "dc_cap" in high.explanation["rules_triggered"]
    zero = cap_line(simple(sales=1050, expected=0, dc=0, mode=SAFE), SAFE)
    assert (zero.qty, zero.status) == (0, Status.OK)
    assert zero.reasons[-1] == f"{REASON_CAPPED} (besoin 1 200, DC 0 → 0)"


def test_excel_parity_never_caps_and_keeps_review_signal() -> None:
    line = cap_line(simple(sales=1050, expected=0, dc=500, mode=EXCEL), EXCEL)
    assert (line.qty, line.status) == (1200, Status.REVIEW)
    assert any("Stock DC insuffisant (500 < 1200)" in r for r in line.reasons)
    assert "dc_cap" not in line.explanation


def test_unknown_dc_stays_review_and_is_not_capped() -> None:
    line = cap_line(simple(sales=1050, expected=0, dc=None, mode=SAFE), SAFE)
    assert (line.qty, line.status) == (1200, Status.REVIEW)
    assert any("SKU absent du stock DC" in r for r in line.reasons)


def test_blocked_line_is_left_alone() -> None:
    line = simple(sales=1050, expected=0, dc=0, mode=SAFE).model_copy(
        update={"status": Status.BLOCKED}
    )
    assert cap_line(line, SAFE).qty == 1200


def test_launch_quantity_is_capped_too_in_safe_mode() -> None:
    sku = "NEW-1"
    inputs = make_inputs(
        stock=[snap(sku, 0)], sales={sku: 0},
        products=[Product(sku=sku, description="VER-New", product_type="C", order_multiple=240)],
        dc={sku: 700},
    )  # fmt: skip
    launch = Launch(boutique="B80", sku=sku, launch_date=date(2026, 10, 10), qty=2400)
    biz = BusinessInputs(launches=(launch,))
    safe = run_planning(inputs, params(), SAFE, dc="CY1", business_inputs=biz)
    ln = next(x for x in safe.lines if x.sku == sku)
    assert (ln.qty, ln.source, ln.status) == (480, QtySource.LAUNCH, Status.REVIEW)  # lancement
    assert ln.reasons[-1] == f"{REASON_CAPPED} (besoin 2 400, DC 700 → 480)"
    par = run_planning(inputs, params(), EXCEL, dc="CY1", business_inputs=biz)
    assert next(x for x in par.lines if x.sku == sku).qty == 2400


def test_override_above_dc_turns_review_in_safe_mode_only() -> None:
    base = cap_line(simple(sales=1050, expected=0, dc=500, mode=SAFE), SAFE)
    ov = Override(sku="7005.70", qty_before=480, qty_after=720, author="Camille",
                  timestamp=datetime(2026, 10, 12, 9, 0))  # fmt: skip
    [line] = apply_overrides([base], [ov])
    assert (line.qty, line.status) == (720, Status.REVIEW)
    assert line.reasons[-1] == f"{REASON_OVER} (DC 500 < 720)"
    within = Override(sku="7005.70", qty_before=480, qty_after=240, author="Camille",
                      timestamp=datetime(2026, 10, 12, 9, 0))  # fmt: skip
    assert apply_overrides([base], [within])[0].status is Status.OK
    excel = simple(sales=400, expected=0, dc=500, mode=EXCEL)  # 480, DC 500
    ov2 = Override(sku="7005.70", qty_before=480, qty_after=720, author="Camille",
                   timestamp=datetime(2026, 10, 12, 9, 0))  # fmt: skip
    assert apply_overrides([excel], [ov2])[0].status is Status.OK  # parité : jamais REVIEW
    assert over_dc(excel, 720) is None
