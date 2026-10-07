"""Ventes (consommation) par SKU. Fonctions pures, aucune I/O.

- Fenêtre principale : les N derniers jours de l'export (N = jours d'historique choisis
  par le planner, 7 par défaut). C'est elle qui sert à la formule Excel.
- Historique de secours : ventes journalières moyennes sur les X dernières semaines où
  le SKU a vendu (X = 4 par défaut). Il ne sert QU'AUX SKU dormants (retour en stock DC).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from app.domain.models import StockMovement


@dataclass(frozen=True)
class SalesSummary:
    """Ventes agrégées sur la fenêtre d'historique.

    `history_days` = nombre de dates distinctes présentes dans la fenêtre (règle Excel S5).
    """

    by_sku: dict[str, float]
    history_days: int
    movement_codes: frozenset[str] | None
    window_days: int | None = None
    period_start: date | None = None
    period_end: date | None = None

    def sales(self, sku: str) -> float:
        return self.by_sku.get(sku, 0.0)


def _filter_codes(
    movements: Iterable[StockMovement], codes: frozenset[str] | None
) -> list[StockMovement]:
    return [m for m in movements if codes is None or m.movement_code in codes]


def aggregate_sales(
    movements: Iterable[StockMovement],
    included_movement_codes: Collection[str] | None = None,
    window_days: int | None = None,
) -> SalesSummary:
    """Somme les quantités par SKU sur la fenêtre.

    `included_movement_codes=None` (défaut voulu) : TOUS les codes comptent, ventes ET
    déstockages, comme dans Excel. `window_days=None` : tout l'export.
    Les dates distinctes sont comptées avant le filtre par code : c'est la longueur de
    la période, pas une propriété d'un code.
    """
    if window_days is not None and window_days < 1:
        raise ValueError("Le nombre de jours d'historique doit être au moins 1")
    codes = frozenset(included_movement_codes) if included_movement_codes is not None else None
    moves = list(movements)
    if not moves:
        return SalesSummary({}, 0, codes, window_days)
    end = max(m.movement_date for m in moves)
    start = (
        end - timedelta(days=window_days - 1)
        if window_days
        else min(m.movement_date for m in moves)
    )
    in_window = [m for m in moves if start <= m.movement_date <= end]
    by_sku: dict[str, float] = {}
    for mv in _filter_codes(in_window, codes):
        by_sku[mv.sku] = by_sku.get(mv.sku, 0.0) + mv.quantity
    return SalesSummary(
        by_sku=by_sku,
        history_days=len({m.movement_date for m in in_window}),
        movement_codes=codes,
        window_days=window_days,
        period_start=start,
        period_end=end,
    )


@dataclass(frozen=True)
class FallbackRate:
    daily_rate: Fraction  # ventes moyennes par jour
    weeks_used: int
    units: float


@dataclass(frozen=True)
class FallbackHistory:
    """Historique de secours, par SKU. `full_weeks` = semaines complètes dans l'export."""

    by_sku: dict[str, FallbackRate]
    weeks_wanted: int
    full_weeks: int
    period_start: date | None
    period_end: date | None

    def rate(self, sku: str) -> FallbackRate | None:
        return self.by_sku.get(sku)


def fallback_history(
    movements: Iterable[StockMovement],
    weeks: int = 4,
    included_movement_codes: Collection[str] | None = None,
) -> FallbackHistory:
    """Moyenne journalière sur les `weeks` dernières semaines où le SKU a vendu.

    Les semaines sont des blocs de 7 jours comptés à rebours depuis la dernière date de
    l'export ; seules les semaines complètes sont utilisées.
    """
    if weeks < 1:
        raise ValueError("Le nombre de semaines de secours doit être au moins 1")
    codes = frozenset(included_movement_codes) if included_movement_codes is not None else None
    moves = list(movements)
    if not moves:
        return FallbackHistory({}, weeks, 0, None, None)
    end = max(m.movement_date for m in moves)
    first = min(m.movement_date for m in moves)
    full_weeks = ((end - first).days + 1) // 7
    blocks: dict[str, dict[int, float]] = {}
    for mv in _filter_codes(moves, codes):
        block = (end - mv.movement_date).days // 7
        if block >= full_weeks:
            continue
        per_sku = blocks.setdefault(mv.sku, {})
        per_sku[block] = per_sku.get(block, 0.0) + mv.quantity
    rates: dict[str, FallbackRate] = {}
    for sku, per_block in blocks.items():
        selling = sorted(b for b, units in per_block.items() if units > 0)[:weeks]
        if not selling:
            continue
        units = sum(per_block[b] for b in selling)
        rates[sku] = FallbackRate(Fraction(units) / (7 * len(selling)), len(selling), units)
    start = end - timedelta(days=7 * full_weeks - 1) if full_weeks else None
    return FallbackHistory(rates, weeks, full_weeks, start, end)
