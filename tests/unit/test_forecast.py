"""Tests unitaires du moteur de quantités (petites données construites à la main)."""

from __future__ import annotations

from datetime import date, timedelta
from fractions import Fraction

import pytest
from pydantic import ValidationError

from app.domain.models import (
    ForecastMode,
    PlanningParameters,
    Product,
    SkuConversion,
    Status,
    StockMovement,
    StockSnapshotLine,
)
from app.engines.conversion import ConversionError, build_conversion_index
from app.engines.forecast import (
    REASON_DC_INSUFFICIENT,
    REASON_INFERRED_MULTIPLE,
    REASON_MISSING_MULTIPLE,
    REASON_NEGATIVE_EXPECTED,
    REASON_NO_ACTIVITY,
    REASON_SUSPECT_MULTIPLE,
    ForecastInputs,
    ceiling_to_multiple,
    compute_line,
    infer_coffee_multiple,
)
from app.engines.sales import aggregate_sales

RUN = date(2026, 10, 5)
DAYS = [RUN - timedelta(days=i) for i in range(1, 8)]  # 7 dates distinctes
EXCEL = ForecastMode.EXCEL_PARITY
SAFE = ForecastMode.STANDARD


def params(cover: float = 7, coffee: float | None = None) -> PlanningParameters:
    return PlanningParameters(
        boutique="B80",
        run_date=RUN,
        delivery_date=RUN,
        cover_days=cover,
        coffee_cover_days=coffee,
    )


def snap(sku: str, expected: float, *, loc: str = "B80", ptype: str = "C", desc: str = "VER-X"):
    return StockSnapshotLine(
        location=loc, sku=sku, description=desc, product_type=ptype,
        expected=expected, available=expected, incoming=0, reserved=0, waiting=0,
    )  # fmt: skip


def mv(sku: str, qty: float, day: date, code: str = "8730") -> StockMovement:
    return StockMovement(
        movement_id=f"{sku}-{day}-{code}", location="B80", sku=sku, product_type="C",
        movement_date=day, movement_code=code, quantity=qty,
    )  # fmt: skip


def make_inputs(
    *,
    stock: list[StockSnapshotLine],
    sales: dict[str, float],
    products: list[Product],
    conversions: list[SkuConversion] = (),  # type: ignore[assignment]
    dc: dict[str, float] | None = None,
) -> ForecastInputs:
    movements = [mv(sku, q, DAYS[0]) for sku, q in sales.items()]
    # Mouvements de remplissage pour avoir 7 dates distinctes.
    movements += [mv("FILLER", 0, d) for d in DAYS]
    dc = dc if dc is not None else {s.sku: 10**9 for s in stock}
    return ForecastInputs(
        boutique_stock={s.sku: s for s in stock},
        dc_stock={sku: snap(sku, q, loc="CY1") for sku, q in dc.items()},
        sales=aggregate_sales(movements),
        products={p.sku: p for p in products},
        conversions=build_conversion_index(conversions),
    )


def simple(
    sku: str = "7005.70",
    *,
    sales: float,
    expected: float,
    multiple: int | None = 240,
    ptype: str = "C",
    desc: str = "VER-Melozio",
    dc: float | None = 10**9,
    mode: ForecastMode = EXCEL,
    p: PlanningParameters | None = None,
):
    inputs = make_inputs(
        stock=[snap(sku, expected, ptype=ptype, desc=desc)],
        sales={sku: sales},
        products=[Product(sku=sku, description=desc, product_type=ptype, order_multiple=multiple)],
        dc={} if dc is None else {sku: dc},
    )
    return compute_line(sku, inputs, p or params(), mode)


# --- Arrondi au multiple -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "multiple", "expected"),
    [
        (Fraction(240), 240, 240),  # cas exact : pas d'arrondi supplémentaire
        (Fraction(241), 240, 480),  # juste au-dessus : multiple suivant
        (Fraction(1, 1000), 240, 240),  # tout besoin positif donne au moins 1 multiple
        (Fraction(0), 240, 0),
        (Fraction(-50), 240, 0),  # besoin négatif : 0
        (Fraction(7, 2), 1, 4),
    ],
)
def test_ceiling_to_multiple(raw: Fraction, multiple: int, expected: int) -> None:
    assert ceiling_to_multiple(raw, multiple) == expected


def test_ceiling_rejects_zero_multiple() -> None:
    with pytest.raises(ValueError):
        ceiling_to_multiple(Fraction(10), 0)


def test_exact_need_is_not_rounded_up_by_float_error() -> None:
    # 7 jours de cover, 7 jours d'historique : besoin = ventes - expected = 480 pile.
    line = simple(sales=720, expected=240)
    assert line.explanation["raw_need"] == 480
    assert line.qty == 480


def test_melozio_formula_value() -> None:
    # Cas réel B80 : 9 j de cover, ventes 8350, Expected 7800 -> 2935,7 -> 3120.
    line = simple(sales=8350, expected=7800, p=params(cover=9))
    assert line.explanation["raw_need"] == pytest.approx(2935.714285714)
    assert line.qty == 3120


# --- Besoin négatif, ventes nulles ---------------------------------------------


def test_negative_need_gives_zero() -> None:
    line = simple(sales=70, expected=1000)
    assert line.qty == 0
    assert line.status is Status.OK
    assert "need_not_positive" in line.explanation["rules_triggered"]


def test_no_sales_no_stock_is_ok_no_activity() -> None:
    line = simple(sales=0, expected=0)
    assert line.qty == 0
    assert line.status is Status.OK
    assert REASON_NO_ACTIVITY in line.reasons
    assert line.explanation["current_cover_days"] is None


def test_no_sales_with_stock_orders_nothing() -> None:
    line = simple(sales=0, expected=50)
    assert line.qty == 0
    assert line.explanation["current_cover_days"] is None


def test_negative_expected_is_review() -> None:
    line = simple(sales=70, expected=-30)
    assert line.status is Status.REVIEW
    assert any(r.startswith(REASON_NEGATIVE_EXPECTED) for r in line.reasons)
    assert line.qty == 240  # 70 + 30 = 100 -> 240


def test_cover_days_in_explanation() -> None:
    line = simple(sales=700, expected=300, multiple=100, ptype="LC", desc="Mug")
    # current = 300 / 700 * 7 = 3 ; besoin = 700 - 300 = 400 ; post = 700/700*7 = 7
    assert line.explanation["current_cover_days"] == pytest.approx(3)
    assert line.qty == 400
    assert line.explanation["post_cover_days"] == pytest.approx(7)


# --- Multiple absent / suspect ---------------------------------------------------


def test_missing_multiple_excel_mode_uses_1_and_flags() -> None:
    line = simple(sales=100, expected=0, multiple=None, mode=EXCEL)
    assert line.qty == 100
    assert line.explanation["multiple_source"] == "excel_default_1"
    assert line.status is Status.REVIEW
    assert not line.qty_reliable
    assert any(r.startswith(REASON_MISSING_MULTIPLE) for r in line.reasons)


@pytest.mark.parametrize(
    ("desc", "multiple"),
    [
        ("VER-Hazelnut Brownie Flavoured", 240),
        ("ORI-Blueberry Cheesecake Flavoured", 800),
        ("VER-Altissio", 360),
        ("VER-Altissio Decaf", 360),
        ("VER-Voltesso", 360),
        ("VER-Diavolitto", 360),
        ("VER-Orafio", 360),
    ],
)
def test_missing_multiple_safe_mode_infers_family(desc: str, multiple: int) -> None:
    line = simple(sales=100, expected=0, multiple=None, desc=desc, mode=SAFE)
    assert line.explanation["multiple"] == multiple
    assert line.explanation["multiple_source"] == "inferred_family"
    assert line.qty == multiple
    assert line.status is Status.REVIEW
    assert line.qty_reliable
    assert any(r.startswith(REASON_INFERRED_MULTIPLE) for r in line.reasons)


def test_missing_multiple_safe_mode_non_coffee_is_unreliable() -> None:
    line = simple(sales=5, expected=0, multiple=None, ptype="LC", desc="Mug", mode=SAFE)
    assert line.qty == 5
    assert line.explanation["multiple_source"] == "missing_default_1"
    assert line.status is Status.REVIEW
    assert not line.qty_reliable


def test_missing_multiple_safe_mode_coffee_without_family_prefix() -> None:
    line = simple(sales=5, expected=0, multiple=None, desc="Advent Calendar", mode=SAFE)
    assert line.explanation["multiple_source"] == "missing_default_1"
    assert not line.qty_reliable


@pytest.mark.parametrize("desc", ["ORI & VER-Monin Syrup", "VER-Mug"])
def test_no_family_inference_for_non_coffee(desc: str) -> None:
    line = simple(sales=5, expected=0, multiple=None, ptype="FB", desc=desc, mode=SAFE)
    assert line.explanation["multiple"] == 1


def test_infer_coffee_multiple_helper() -> None:
    assert infer_coffee_multiple("  ver-Melozio") == 240
    assert infer_coffee_multiple("ORI & VER-Tumbler") is None
    assert infer_coffee_multiple(None) is None


def test_coffee_with_multiple_1_is_suspect() -> None:
    line = simple(sales=10, expected=0, multiple=1)
    assert line.status is Status.REVIEW
    assert any(r.startswith(REASON_SUSPECT_MULTIPLE) for r in line.reasons)
    assert line.qty == 10  # même quantité qu'Excel, mais signalée


def test_accessory_with_multiple_1_is_ok() -> None:
    line = simple(sales=10, expected=0, multiple=1, ptype="LC", desc="Mug")
    assert line.status is Status.OK


@pytest.mark.parametrize(("mode", "qty"), [(EXCEL, 0), (SAFE, 10)])
def test_zero_multiple(mode: ForecastMode, qty: int) -> None:
    # Excel : CEILING(x; 0) = 0. Safe : traité comme un multiple manquant.
    line = simple(sales=10, expected=0, multiple=0, ptype="M", desc="Machine", mode=mode)
    assert line.qty == qty
    assert line.status is Status.REVIEW
    assert not line.qty_reliable


# --- Cover café / non-café -------------------------------------------------------


def test_coffee_cover_applies_only_to_coffee() -> None:
    p = params(cover=7, coffee=14)
    coffee = simple(sales=70, expected=0, multiple=10, p=p)
    other = simple(sales=70, expected=0, multiple=10, ptype="LC", desc="Mug", p=p)
    assert coffee.explanation["cover_source"] == "coffee_cover_days"
    assert coffee.qty == 140
    assert other.explanation["cover_source"] == "cover_days"
    assert other.qty == 70


def test_without_coffee_cover_coffee_uses_cover_days() -> None:
    line = simple(sales=70, expected=0, multiple=10, p=params(cover=7, coffee=None))
    assert line.explanation["cover_source"] == "cover_days"
    assert line.qty == 70


# --- Exceptions et conversions ---------------------------------------------------


def test_7010_is_no_longer_hardcoded_in_engine() -> None:
    # Décision A8 : l'exception vit dans le référentiel des exclusions, plus dans le moteur.
    line = simple("7010.70", sales=700, expected=0, multiple=10)
    assert line.qty == 700


def _conversion_inputs(effective: date, new_sku: str = "7934.70") -> ForecastInputs:
    return make_inputs(
        stock=[snap("7872.70", 30), snap("7934.70", 100)],
        sales={"7872.70": 70, "7934.70": 140},
        products=[
            Product(sku="7872.70", product_type="C", order_multiple=10),
            Product(sku="7934.70", product_type="C", order_multiple=10),
        ],
        conversions=[SkuConversion(old_sku="7872.70", new_sku=new_sku, effective_date=effective)],
    )


def test_old_sku_still_orderable_on_effective_day() -> None:
    inputs = _conversion_inputs(effective=RUN)  # J : pas encore bloqué
    line = compute_line("7872.70", inputs, params(), EXCEL)
    assert "old_sku_blocked" not in line.explanation["rules_triggered"]
    assert line.qty == 40  # 70 - 30
    assert line.status is Status.REVIEW  # conversion imminente signalée


def test_old_sku_blocked_from_day_after() -> None:
    inputs = _conversion_inputs(effective=RUN - timedelta(days=1))  # J+1
    line = compute_line("7872.70", inputs, params(), EXCEL)
    assert line.qty == 0
    assert line.status is Status.BLOCKED
    assert any("7934.70" in r for r in line.reasons)


def test_new_sku_aggregates_old_sku_sales_and_expected() -> None:
    inputs = _conversion_inputs(effective=RUN - timedelta(days=10))
    line = compute_line("7934.70", inputs, params(), EXCEL)
    inp = line.explanation["inputs"]
    assert inp["sales_total"] == 210  # 140 + 70
    assert inp["expected_total"] == 130  # 100 + 30
    assert line.qty == 80  # 210 - 130


def test_no_replacement_xyz123() -> None:
    inputs = make_inputs(
        stock=[snap("7990.70", 0)],
        sales={"7990.70": 50},
        products=[Product(sku="7990.70", product_type="C", order_multiple=800)],
        conversions=[
            SkuConversion(old_sku="7990.70", new_sku="XYZ123", effective_date=date(2026, 7, 14))
        ],
    )
    line = compute_line("7990.70", inputs, params(), EXCEL)
    assert line.qty == 0
    assert line.status is Status.BLOCKED
    assert "XYZ123" in line.reasons[-1]
    assert "XYZ123" not in inputs.conversions.by_new


def test_conversion_chains_are_refused() -> None:
    d = date(2026, 9, 1)
    with pytest.raises(ConversionError):
        build_conversion_index(
            [
                SkuConversion(old_sku="A", new_sku="B", effective_date=d),
                SkuConversion(old_sku="B", new_sku="C", effective_date=d),
            ]
        )


# --- Stock DC --------------------------------------------------------------------


def test_dc_insufficient_flags_without_capping() -> None:
    line = simple(sales=1000, expected=0, multiple=100, dc=150)
    assert line.qty == 1000  # jamais plafonné
    assert line.status is Status.REVIEW
    assert any(r.startswith(REASON_DC_INSUFFICIENT) for r in line.reasons)


def test_dc_absent_flags_when_ordering() -> None:
    line = simple(sales=100, expected=0, multiple=100, dc=None)
    assert line.explanation["inputs"]["dc_available"] is None
    assert line.status is Status.REVIEW


def test_dc_absent_is_fine_when_not_ordering() -> None:
    line = simple(sales=0, expected=10, multiple=100, dc=None)
    assert line.status is Status.OK


# --- SKU en texte ----------------------------------------------------------------


@pytest.mark.parametrize("sku", ["0005452", "3517/BULK", "7005.70", "J620-US-ME-BV"])
def test_sku_text_is_preserved(sku: str) -> None:
    line = simple(sku, sales=10, expected=0, multiple=1, ptype="LC", desc="X")
    assert line.sku == sku
    assert isinstance(line.sku, str)


def test_sku_number_is_refused() -> None:
    with pytest.raises(ValidationError):
        Product(sku=7005.70)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Product(sku=5452)  # type: ignore[arg-type]


def test_missing_from_stock_situation_is_review() -> None:
    inputs = make_inputs(
        stock=[], sales={"7938.70": 10}, products=[Product(sku="7938.70", order_multiple=10)]
    )
    line = compute_line("7938.70", inputs, params(), EXCEL)
    assert line.status is Status.REVIEW
    assert line.explanation["inputs"]["expected_own"] == 0


# --- Paramètres obligatoires -----------------------------------------------------


def test_parameters_have_no_defaults() -> None:
    with pytest.raises(ValidationError):
        PlanningParameters(boutique="B80", run_date=RUN, delivery_date=RUN, cover_days=9)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        PlanningParameters(boutique="B80", run_date=RUN, delivery_date=RUN, coffee_cover_days=None)  # type: ignore[call-arg]


def test_explanation_carries_trace() -> None:
    line = simple(sales=8350, expected=7800, p=params(cover=9))
    exp = line.explanation
    for key in (
        "rule_version", "mode", "parameters", "inputs", "cover_applied", "raw_need",
        "multiple", "qty", "current_cover_days", "post_cover_days", "rules_triggered",
    ):  # fmt: skip
        assert key in exp
    assert exp["parameters"]["movement_codes"] == "ALL"
