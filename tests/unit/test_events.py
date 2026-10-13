"""Événements SKU : fenêtre active, ciblage, effets, priorités, conflits, portée, export."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.domain.events import (
    ALL,
    EventEffect,
    EventQty,
    EventScope,
    EventSku,
    EventStatus,
    EventType,
    SkuEvent,
)
from app.domain.models import (
    Allocation,
    ForecastMode,
    Override,
    PlanningParameters,
    Product,
    QtySource,
    Status,
)
from app.engines.events import (
    ACTIVE,
    ENDED,
    LEAD,
    REASON_CONFLICT,
    UPCOMING,
    export_check,
    select_for_order,
    store_requests_done,
    time_state,
)
from app.services.planning import BusinessInputs, EventContext, run_planning
from tests.unit.test_forecast import RUN, make_inputs, snap

DELIVERY = date(2026, 10, 8)


def params(delivery: date = DELIVERY) -> PlanningParameters:
    return PlanningParameters(boutique="B80", run_date=RUN, delivery_date=delivery, cover_days=7,
                              coffee_cover_days=None)  # fmt: skip


def inputs():  # noqa: ANN201
    stock = [snap("7005.70", 100), snap("7921.70", 100, desc="ORI-Y"), snap("7039.70", 0)]
    products = [
        Product(sku="7005.70", description="VER-Melozio", product_type="C", order_multiple=240),
        Product(sku="7921.70", description="ORI-Y", product_type="C", order_multiple=800),
        Product(sku="7039.70", description="VER-Old", product_type="C", order_multiple=240),
    ]
    # 7005.70 : 7 × 700 / 7 − 100 = 600 → 720 (multiple 240)
    return make_inputs(stock=stock, sales={"7005.70": 700, "7921.70": 70, "7039.70": 140},
                       products=products)  # fmt: skip


def ev(**kw) -> SkuEvent:  # noqa: ANN003
    base = dict(id=1, scope=EventScope.TEAM, type=EventType.PROMOTION, title="Festive AOS",
                skus=(EventSku(sku="7005.70"),), start=DELIVERY, end=date(2026, 10, 14),
                effect=EventEffect.FLAG, planner="Camille")  # fmt: skip
    return SkuEvent(**{**base, **kw})


def plan(*events: SkuEvent, planner: str = "Camille", taken: frozenset[int] = frozenset(),
         delivery: date = DELIVERY, mode: ForecastMode = ForecastMode.STANDARD,
         business: BusinessInputs | None = None):  # noqa: ANN201  # fmt: skip
    res = run_planning(inputs(), params(delivery), mode, dc="CY1", business_inputs=business,
                       event_context=EventContext(tuple(events), planner, taken))  # fmt: skip
    return res, {x.sku: x for x in res.base_lines}


def qty(*q: tuple[str, str, int]) -> tuple[EventQty, ...]:
    return tuple(EventQty(boutique=b, sku=s, qty=n) for b, s, n in q)


# --- Fenêtre active ----------------------------------------------------------------


def test_active_upcoming_ended_with_lead_days() -> None:
    e = ev(lead_days=3)  # flagué dès le 05/10
    assert time_state(e, date(2026, 10, 4)) == UPCOMING
    assert time_state(e, date(2026, 10, 5)) == LEAD
    assert time_state(e, date(2026, 10, 8)) == ACTIVE
    assert time_state(e, date(2026, 10, 14)) == ACTIVE
    assert time_state(e, date(2026, 10, 15)) == ENDED
    sel = dict(boutique="B80", dc="CY1", planner="Camille")
    assert select_for_order([e], delivery=date(2026, 10, 5), **sel) == [e]
    assert select_for_order([e], delivery=date(2026, 10, 4), **sel) == []
    assert select_for_order([e], delivery=date(2026, 10, 15), **sel) == []
    assert select_for_order([e.model_copy(update={"status": EventStatus.DRAFT})],
                            delivery=DELIVERY, **sel) == []  # fmt: skip


def test_launch_defaults_to_7_lead_days_and_end_is_optional_for_every_type() -> None:
    from app.domain.events import default_lead_days

    assert default_lead_days(EventType.LAUNCH) == 7
    assert default_lead_days(EventType.PROMOTION) == 0
    # D-053 : fin vide = sans fin, pour tous les types (début obligatoire).
    assert ev(end=None).in_window(date(2030, 1, 1))
    assert ev(type=EventType.BAN, effect=EventEffect.BLOCK, end=None).in_window(date(2030, 1, 1))
    conv = ev(type=EventType.CONVERSION, effect=EventEffect.REPLACE, end=None,
              skus=(EventSku(sku="7039.70", new_sku="7005.70"),))  # fmt: skip
    assert conv.end is None and not conv.in_window(date(2026, 10, 1))
    with pytest.raises(ValidationError, match="avant la date de début"):
        ev(end=date(2026, 10, 1))


def test_flag_is_applied_in_the_lead_window_with_a_note() -> None:
    _, lines = plan(ev(start=date(2026, 10, 10), lead_days=2))
    assert lines["7005.70"].status is Status.REVIEW
    assert any("anticipation : début le 10/10" in r for r in lines["7005.70"].reasons)


# --- Ciblage ------------------------------------------------------------------------


def test_targeted_boutique_vs_all_vs_dc() -> None:
    assert plan(ev(boutiques=("B19",)))[1]["7005.70"].status is Status.OK
    assert plan(ev(boutiques=("B19", "B80")))[1]["7005.70"].status is Status.REVIEW
    assert plan(ev(dcs=("CY1",)))[1]["7005.70"].status is Status.REVIEW
    assert plan(ev(dcs=("MT1",)))[1]["7005.70"].status is Status.OK
    assert plan(ev())[1]["7005.70"].status is Status.REVIEW  # toutes


# --- Effets -------------------------------------------------------------------------


def test_flag_keeps_the_engine_quantity() -> None:
    res, lines = plan(ev())
    line = lines["7005.70"]
    assert (line.qty, line.status, line.source) == (720, Status.REVIEW, QtySource.FORECAST)
    assert any("Promotion « Festive AOS » (Équipe) : à vérifier" in r for r in line.reasons)
    assert line.explanation["events"]["applied"][0]["title"] == "Festive AOS"
    assert [e.title for e in res.events] == ["Festive AOS"]


def test_imposed_quantity_is_rounded_to_the_multiple() -> None:
    e = ev(effect=EventEffect.SET_QTY, quantities=qty((ALL, ALL, 1000)))
    line = plan(e)[1]["7005.70"]
    assert (line.qty, line.status, line.source) == (1200, Status.REVIEW, QtySource.EVENT)
    assert line.forecast_qty == 720
    assert any("quantité imposée 1000 (arrondi au multiple de 240 : 1200)" in r
               for r in line.reasons)  # fmt: skip


def test_most_precise_quantity_wins_and_missing_quantity_is_review() -> None:
    e = ev(effect=EventEffect.SET_QTY,
           quantities=qty(("B80", ALL, 240), (ALL, ALL, 960)))  # fmt: skip
    assert plan(e)[1]["7005.70"].qty == 240
    e = ev(effect=EventEffect.SET_QTY, quantities=qty(("B19", ALL, 240)))
    line = plan(e)[1]["7005.70"]
    assert (line.qty, line.status) == (720, Status.REVIEW)
    assert any("quantité non définie pour B80" in r for r in line.reasons)


def test_added_quantity_is_rounded_and_added_to_the_need() -> None:
    e = ev(effect=EventEffect.ADD_QTY, quantities=qty((ALL, ALL, 100)))
    line = plan(e)[1]["7005.70"]
    assert (line.qty, line.status, line.source) == (960, Status.REVIEW, QtySource.EVENT)


def test_block_forces_zero_and_blocked() -> None:
    e = ev(type=EventType.BAN, title="Rappel qualité", effect=EventEffect.BLOCK, end=None)
    line = plan(e)[1]["7005.70"]
    assert (line.qty, line.status, line.source) == (0, Status.BLOCKED, QtySource.EVENT)


def test_ban_always_wins_over_a_promotion() -> None:
    ban = ev(id=2, type=EventType.BAN, title="Rappel", effect=EventEffect.BLOCK, end=None)
    promo = ev(effect=EventEffect.SET_QTY, quantities=qty((ALL, ALL, 480)))
    line = plan(promo, ban)[1]["7005.70"]
    assert (line.qty, line.status) == (0, Status.BLOCKED)
    assert any("Le blocage prime sur Promotion « Festive AOS »" in r for r in line.reasons)


def test_two_contradictory_events_block_the_line() -> None:
    a = ev(effect=EventEffect.SET_QTY, quantities=qty((ALL, ALL, 480)))
    b = ev(id=2, title="Urgence Noël", type=EventType.URGENT, effect=EventEffect.ADD_QTY,
           quantities=qty((ALL, ALL, 240)))  # fmt: skip
    line = plan(a, b)[1]["7005.70"]
    assert (line.qty, line.status) == (0, Status.BLOCKED)
    assert any(r.startswith(REASON_CONFLICT) for r in line.reasons)
    # Même quantité imposée deux fois : pas de contradiction.
    c = b.model_copy(update={"effect": EventEffect.SET_QTY, "quantities": a.quantities})
    assert plan(a, c)[1]["7005.70"].qty == 480


def test_two_added_quantities_are_summed_in_review() -> None:
    """Q-027 : promo + demande boutique qui ajoutent chacune → additionnées, REVIEW."""
    promo = ev(effect=EventEffect.ADD_QTY, quantities=qty((ALL, ALL, 100)))
    demande = ev(id=2, title="Demande B80", type=EventType.STORE_REQUEST, scope=EventScope.PERSONAL,
                 boutiques=("B80",),
                 effect=EventEffect.ADD_QTY, quantities=qty((ALL, ALL, 240)))  # fmt: skip
    line = plan(promo, demande)[1]["7005.70"]
    # 720 calculé + 100 arrondi à 240 + 240 = 1200
    assert (line.qty, line.status, line.source) == (1200, Status.REVIEW, QtySource.EVENT)
    assert line.explanation["events"]["decision"] == "add_qty_sum"
    assert not any(r.startswith(REASON_CONFLICT) for r in line.reasons)
    assert any("Festive AOS" in r and "100 ajouté" in r for r in line.reasons)
    assert any("Demande B80" in r and "240 ajouté" in r for r in line.reasons)
    assert any("720 calculé + 240 + 240 = 1200" in r for r in line.reasons)
    # Même quantité ajoutée par deux événements : additionnée aussi (240 + 240).
    same = demande.model_copy(update={"quantities": promo.quantities})
    assert plan(promo, same)[1]["7005.70"].qty == 1200


def test_other_quantity_conflicts_stay_blocked() -> None:
    """Q-027 : imposée contre imposée (différentes), imposée contre ajoutée → BLOCKED."""
    a = ev(effect=EventEffect.SET_QTY, quantities=qty((ALL, ALL, 480)))
    b = ev(id=2, title="Autre", effect=EventEffect.SET_QTY, quantities=qty((ALL, ALL, 960)))
    line = plan(a, b)[1]["7005.70"]
    assert (line.qty, line.status) == (0, Status.BLOCKED)
    assert any(r.startswith(REASON_CONFLICT) for r in line.reasons)
    add = ev(id=3, title="Ajout", effect=EventEffect.ADD_QTY, quantities=qty((ALL, ALL, 240)))
    ban = ev(id=4, type=EventType.BAN, title="Rappel", effect=EventEffect.BLOCK, end=None)
    assert plan(add, add.model_copy(update={"id": 5}), ban)[1]["7005.70"].status is Status.BLOCKED


def test_conversion_blocks_old_and_aggregates_history_on_new() -> None:
    e = ev(type=EventType.CONVERSION, title="Melozio nouvelle recette",
           effect=EventEffect.REPLACE,
           skus=(EventSku(sku="7039.70", new_sku="7005.70"),))  # fmt: skip
    _, lines = plan(e)
    old, new = lines["7039.70"], lines["7005.70"]
    assert (old.qty, old.status) == (0, Status.BLOCKED)
    assert new.explanation["inputs"]["sales_from_old_skus"] == {"7039.70": 140}
    # 7 × 840 / 7 − 100 = 740 → 960
    assert (new.qty, new.status) == (960, Status.REVIEW)
    assert any("remplace 7039.70" in r for r in new.reasons)


def test_launch_absent_from_stock_situation_is_still_added() -> None:
    launch = ev(type=EventType.LAUNCH, title="Advent", effect=EventEffect.SET_QTY,
                skus=(EventSku(sku="9999.70", multiple=240),),
                quantities=qty((ALL, ALL, 300)))  # fmt: skip
    line = plan(launch)[1]["9999.70"]
    assert (line.qty, line.status, line.source) == (480, Status.REVIEW, QtySource.EVENT)
    assert line.explanation["multiple_source"] == "event"
    flag_only = launch.model_copy(update={"effect": EventEffect.FLAG, "quantities": ()})
    line = plan(flag_only)[1]["9999.70"]
    assert (line.qty, line.status) == (0, Status.REVIEW)  # jamais inventé, jamais oublié
    assert any("absent de la Stock Situation" in r for r in line.reasons)


def test_official_allocation_is_never_overwritten() -> None:
    alloc = Allocation(boutique="B80", sku="7005.70", total_qty=480, already_sent=0,
                       wave_plan=(100,), current_wave=1)  # fmt: skip
    e = ev(effect=EventEffect.SET_QTY, quantities=qty((ALL, ALL, 2400)))
    line = plan(e, business=BusinessInputs(allocations=(alloc,)))[1]["7005.70"]
    assert (line.qty, line.source, line.status) == (480, QtySource.ALLOCATION, Status.REVIEW)
    assert any("Allocation officielle conservée" in r for r in line.reasons)


# --- Portée Équipe / Personnel ---------------------------------------------------------


def test_personal_event_of_planner_a_does_not_affect_planner_b_until_taken_over() -> None:
    req = ev(id=7, scope=EventScope.PERSONAL, type=EventType.STORE_REQUEST, title="Demande B80",
             boutiques=("B80",), effect=EventEffect.SET_QTY, quantities=qty((ALL, ALL, 240)),
             end=None, until_next_order=True, requested_by="Gérante B80")  # fmt: skip
    assert plan(req, planner="Camille")[1]["7005.70"].qty == 240
    res, lines = plan(req, planner="Sami")
    assert lines["7005.70"].qty == 720 and res.events == ()
    assert plan(req, planner="Sami", taken=frozenset({7}))[1]["7005.70"].qty == 240
    assert store_requests_done(plan(req)[0].events) == [req]


def test_personal_types_and_planner_are_enforced() -> None:
    with pytest.raises(ValidationError, match="réservé à la portée Personnel"):
        ev(type=EventType.WATCH)
    with pytest.raises(ValidationError, match="doit avoir son planner"):
        ev(scope=EventScope.PERSONAL, type=EventType.WATCH, planner="")
    with pytest.raises(ValidationError, match="impossible pour le type"):
        ev(type=EventType.BAN, effect=EventEffect.FLAG, end=None)
    with pytest.raises(ValidationError, match="une seule boutique"):
        ev(scope=EventScope.PERSONAL, type=EventType.STORE_REQUEST, effect=EventEffect.SET_QTY,
           quantities=qty((ALL, ALL, 1)), boutiques=("B80", "B19"))  # fmt: skip


def test_skus_stay_text() -> None:
    with pytest.raises(ValidationError):
        EventSku(sku=7005.7)  # type: ignore[arg-type]
    e = ev(skus=(EventSku(sku="0005452"), EventSku(sku="3517/BULK")))
    assert e.sku_codes() == ["0005452", "3517/BULK"]


# --- Garde-fou avant export ------------------------------------------------------------


def test_export_refused_until_blocked_lines_are_treated() -> None:
    ban = ev(type=EventType.BAN, effect=EventEffect.BLOCK, end=None)
    launch = ev(id=2, type=EventType.LAUNCH, title="Advent", effect=EventEffect.FLAG,
                skus=(EventSku(sku="7921.70"),))  # fmt: skip
    res, _ = plan(ban, launch)
    check = export_check(res.base_lines, overrides={}, seen=set(), confirmed_blocks=set(),
                         include_confirmed=False)  # fmt: skip
    assert check.blocked == ("7005.70",) and check.unvalidated == ("7921.70",)
    assert check.to_include == (("7921.70", "Advent"),) and not check.ok
    # override SANS raison : pas « justifié »
    ov = Override(sku="7005.70", qty_before=0, qty_after=240, category="other", author="Camille",
                  timestamp=datetime(2026, 10, 5, 9))  # fmt: skip
    check = export_check(res.base_lines, overrides={"7005.70": ov}, seen={"7921.70"},
                         confirmed_blocks=set(), include_confirmed=True)  # fmt: skip
    assert check.blocked == ("7005.70",) and not check.ok
    justified = ov.model_copy(update={"reason": "Accord qualité reçu"})
    check = export_check(res.base_lines, overrides={"7005.70": justified}, seen={"7921.70"},
                         confirmed_blocks=set(), include_confirmed=True)  # fmt: skip
    assert check.ok
    check = export_check(res.base_lines, overrides={}, seen={"7921.70"},
                         confirmed_blocks={"7005.70"}, include_confirmed=True)  # fmt: skip
    assert check.ok


# --- Sans événement actif : rien ne change ---------------------------------------------


@pytest.mark.parametrize("mode", [ForecastMode.STANDARD, ForecastMode.EXCEL_PARITY])
def test_inactive_events_change_nothing(mode: ForecastMode) -> None:
    inactive = (
        ev(boutiques=("B19",), effect=EventEffect.SET_QTY, quantities=qty((ALL, ALL, 9600))),
        ev(id=2, start=date(2026, 11, 1), end=date(2026, 11, 5)),
        ev(id=3, status=EventStatus.CANCELLED),
        ev(id=4, scope=EventScope.PERSONAL, type=EventType.WATCH, planner="Léa"),
    )
    without = plan(mode=mode)[0]
    with_events = plan(*inactive, mode=mode)[0]
    assert with_events.lines == without.lines and with_events.events == ()
