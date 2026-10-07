"""Agrégation des ventes (consommation) par SKU. Fonctions pures, aucune I/O."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from app.domain.models import StockMovement


@dataclass(frozen=True)
class SalesSummary:
    """Ventes agrégées sur la période d'historique.

    `history_days` = nombre de dates distinctes présentes dans les mouvements
    (même règle qu'Excel, cellule S5).
    """

    by_sku: dict[str, float]
    history_days: int
    movement_codes: frozenset[str] | None

    def sales(self, sku: str) -> float:
        return self.by_sku.get(sku, 0.0)


def aggregate_sales(
    movements: Iterable[StockMovement],
    included_movement_codes: Collection[str] | None = None,
) -> SalesSummary:
    """Somme les quantités par SKU.

    `included_movement_codes=None` (défaut voulu) : TOUS les codes comptent,
    ventes ET déstockages, comme dans Excel. Une liste permet de restreindre.

    Les dates distinctes sont comptées sur l'ensemble des mouvements, avant le
    filtre par code : c'est la longueur de la période, pas une propriété d'un code.
    """
    codes = frozenset(included_movement_codes) if included_movement_codes is not None else None
    by_sku: dict[str, float] = {}
    dates = set()
    for mv in movements:
        dates.add(mv.movement_date)
        if codes is not None and mv.movement_code not in codes:
            continue
        by_sku[mv.sku] = by_sku.get(mv.sku, 0.0) + mv.quantity
    return SalesSummary(by_sku=by_sku, history_days=len(dates), movement_codes=codes)
