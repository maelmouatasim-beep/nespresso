"""Boutiques qui commandent un jour donné, d'après l'onglet Schedule. Fonctions pures.

Seuls les jours de commande et de livraison sont utilisés : les cover days du
Schedule ne le sont jamais (CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.models import WEEKDAYS, ScheduleSlot


def parse_weekday(text: str | None) -> int | None:
    """« Monday », « MONDAY », « mon », « lundi » → 0. None si illisible."""
    t = (text or "").strip().lower()
    if not t:
        return None
    french = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    for i, (en, fr) in enumerate(zip(WEEKDAYS, french, strict=True)):
        if t in (en, fr) or (len(t) >= 3 and (en.startswith(t) or fr.startswith(t))):
            return i
    return None


def next_delivery_date(run_date: date, delivery_weekday: int) -> date:
    """Prochaine date (strictement après le run) qui tombe le jour de livraison."""
    ahead = (delivery_weekday - run_date.weekday()) % 7
    return run_date + timedelta(days=ahead or 7)


@dataclass(frozen=True)
class DaySlot:
    boutique: str
    delivery_date: date
    carrier: str | None


def boutiques_ordering_on(slots: Iterable[ScheduleSlot], run_date: date) -> list[DaySlot]:
    """Boutiques dont un créneau de commande tombe le jour du run, avec la livraison prévue."""
    out: dict[str, DaySlot] = {}
    for s in slots:
        if s.order_weekday == run_date.weekday() and s.boutique not in out:
            out[s.boutique] = DaySlot(
                s.boutique, next_delivery_date(run_date, s.delivery_weekday), s.carrier
            )
    return sorted(out.values(), key=lambda d: d.boutique)
