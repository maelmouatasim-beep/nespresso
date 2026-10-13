"""Allocations, lancements et stocks cibles saisis comme événements SKU (D-042)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.events import ALLOWED_EFFECTS, EventEffect, EventQty, EventSku, EventType
from app.domain.models import Allocation, Launch, QtySource, Status, TargetStock
from app.engines.events import REASON_CONFLICT, export_check
from app.ingestion.business_events import events_from_business
from app.services.planning import BusinessInputs
from tests.unit.test_events import DELIVERY, ev, plan
from tests.unit.test_forecast import RUN

ALLOC = Allocation(boutique="B80", sku="7005.70", total_qty=4800, already_sent=0,
                   wave_plan=(75, 25), current_wave=1)  # fmt: skip
LAUNCH = Launch(boutique="B80", sku="7921.70", launch_date=date(2026, 10, 14), qty=3600)
TARGET = TargetStock(boutique="B80", sku="7039.70", min_qty=300, target_qty=500, max_qty=900)
LEGACY = BusinessInputs(allocations=(ALLOC,), launches=(LAUNCH,), targets=(TARGET,))


def alloc_event(total: int = 4800, **kw) -> object:  # noqa: ANN003
    return ev(effect=EventEffect.ALLOCATION, end=None, quantities=(
        EventQty(boutique="B80", sku="7005.70", qty=total, waves=(75, 25), current_wave=1),
    ), **kw)  # fmt: skip


def test_new_type_and_effects_are_offered() -> None:
    assert EventEffect.ALLOCATION in ALLOWED_EFFECTS[EventType.PROMOTION]
    assert EventEffect.INITIAL_QTY in ALLOWED_EFFECTS[EventType.LAUNCH]
    assert ALLOWED_EFFECTS[EventType.TARGET_STOCK] == (EventEffect.TARGET,)
    # Les défauts proposés ne changent pas.
    assert ALLOWED_EFFECTS[EventType.PROMOTION][0] is EventEffect.FLAG
    assert ALLOWED_EFFECTS[EventType.LAUNCH][0] is EventEffect.SET_QTY


def test_quantity_fields_are_checked() -> None:
    with pytest.raises(ValidationError, match="totaliser 100"):
        ev(effect=EventEffect.ALLOCATION, quantities=(EventQty(qty=10, waves=(50, 25)),))
    with pytest.raises(ValidationError, match="vague en cours"):
        ev(effect=EventEffect.ALLOCATION, quantities=(EventQty(qty=10, current_wave=2),))
    with pytest.raises(ValidationError, match="min et max"):
        ev(type=EventType.TARGET_STOCK, effect=EventEffect.TARGET, quantities=(EventQty(qty=30),))
    with pytest.raises(ValidationError, match="min ≤ cible ≤ max"):
        ev(type=EventType.TARGET_STOCK, effect=EventEffect.TARGET,
           quantities=(EventQty(qty=30, min_qty=40, max_qty=60),))  # fmt: skip
    with pytest.raises(ValidationError, match="au moins une quantité"):
        ev(type=EventType.TARGET_STOCK, effect=EventEffect.TARGET)
    # Fin vide permise pour tous les types (D-053).
    assert alloc_event().end is None
    assert ev(type=EventType.LAUNCH, effect=EventEffect.INITIAL_QTY, end=None,
              quantities=(EventQty(qty=10),)).end is None  # fmt: skip


def test_allocation_event_gives_exactly_the_old_allocation() -> None:
    old = plan(business=BusinessInputs(allocations=(ALLOC,)))[1]["7005.70"]
    line = plan(alloc_event())[1]["7005.70"]
    assert (line.qty, line.source) == (old.qty, old.source) == (3600, QtySource.ALLOCATION)
    assert any("Source : Promotion « Festive AOS »" in r for r in line.reasons)
    assert line.explanation["business"]["allocation"]["wave_plan"] == [75, 25]


def test_initial_qty_and_target_events_reuse_the_business_rules() -> None:
    launch = ev(id=2, type=EventType.LAUNCH, effect=EventEffect.INITIAL_QTY, skus=(
        EventSku(sku="7921.70"),), start=date(2026, 10, 14), end=date(2026, 10, 14), lead_days=7,
        quantities=(EventQty(qty=3600),))  # fmt: skip
    target = ev(id=3, type=EventType.TARGET_STOCK, effect=EventEffect.TARGET,
                skus=(EventSku(sku="7039.70"),), end=None,
                quantities=(EventQty(qty=500, min_qty=300, max_qty=900),))  # fmt: skip
    old = plan(business=LEGACY)[1]
    new = plan(alloc_event(), launch, target)[1]
    for sku in ("7005.70", "7921.70", "7039.70"):
        assert (new[sku].qty, new[sku].source) == (old[sku].qty, old[sku].source), sku
    assert new["7921.70"].source is QtySource.LAUNCH
    assert new["7039.70"].source is QtySource.TARGET_STOCK


def test_missing_quantity_for_the_boutique_is_review() -> None:
    e = ev(effect=EventEffect.ALLOCATION, end=None, quantities=(EventQty(boutique="B19", qty=480),))
    line = plan(e)[1]["7005.70"]
    assert line.status is Status.REVIEW and line.source is QtySource.FORECAST
    assert any("quantité non définie pour B80" in r for r in line.reasons)


def test_two_different_allocations_block_and_identical_ones_count_once() -> None:
    line = plan(alloc_event(), alloc_event(id=2, title="Autre"))[1]["7005.70"]
    assert (line.qty, line.source) == (3600, QtySource.ALLOCATION)
    line = plan(alloc_event(), alloc_event(2400, id=2, title="Autre"))[1]["7005.70"]
    assert (line.qty, line.status) == (0, Status.BLOCKED)
    assert any(REASON_CONFLICT in r for r in line.reasons)
    # Un événement contre un ancien fichier pas encore converti : même règle.
    line = plan(alloc_event(2400), business=BusinessInputs(allocations=(ALLOC,)))[1]["7005.70"]
    assert line.status is Status.BLOCKED and any("ancien fichier" in r for r in line.reasons)


def test_a_ban_still_wins_over_an_allocation_event() -> None:
    ban = ev(id=2, type=EventType.BAN, effect=EventEffect.BLOCK, end=None, title="Interdit")
    line = plan(alloc_event(), ban)[1]["7005.70"]
    assert (line.qty, line.status) == (0, Status.BLOCKED)
    assert any("Le blocage prime" in r for r in line.reasons)


def test_allocation_event_lines_must_be_seen_before_export() -> None:
    res, lines = plan(alloc_event())
    check = export_check(res.base_lines, overrides={}, seen=(), confirmed_blocks=(),
                         include_confirmed=False)  # fmt: skip
    assert "7005.70" in check.unvalidated


def test_migration_keeps_every_value_and_the_same_order() -> None:
    events = events_from_business((ALLOC,), (LAUNCH,), (TARGET,), author="Camille", today=RUN)
    assert [e.type for e in events] == [EventType.PROMOTION, EventType.LAUNCH,
                                        EventType.TARGET_STOCK]  # fmt: skip
    events = [e.model_copy(update={"id": i}) for i, e in enumerate(events, 1)]
    old = plan(business=LEGACY)[1]
    new = plan(*events)[1]
    for sku in ("7005.70", "7921.70", "7039.70"):
        assert (new[sku].qty, new[sku].source) == (old[sku].qty, old[sku].source), sku
    launch = events[1]
    assert (launch.start, launch.end) == (LAUNCH.launch_date, LAUNCH.launch_date)
    assert launch.in_window(DELIVERY) and launch.in_window(RUN)


def test_migration_refuses_a_non_integer_target() -> None:
    bad = TARGET.model_copy(update={"min_qty": 2.5})
    with pytest.raises(ValueError, match="entiers"):
        events_from_business((), (), (bad,), author="Camille", today=RUN)
