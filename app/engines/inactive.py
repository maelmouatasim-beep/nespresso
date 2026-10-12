"""SKU inactifs (anciens SKU) masqués dans la table de commande (D-049).

Règle du planner (12-oct-2026) : un SKU avec Expected = 0, Available = 0, Incoming = 0,
aucune vente sur la période ET stock DC = 0 (ou absent) dans le DC de la boutique est un
ancien SKU. Il reste calculé (parité Excel inchangée), mais n'apparaît pas dans la table et
ne compte pas dans les tuiles. Exceptions, toujours affichées : un SKU avec un événement
actif, ou nouveau SKU d'une conversion active. Fonctions pures, sans I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from app.domain.models import StockMovement, StockSnapshotLine


def is_inactive(
    *,
    expected: float,
    available: float,
    incoming: float,
    has_sales: bool,
    dc_available: float | None,
    new_sku: bool = False,
) -> bool:
    """Vrai si tout est à zéro en boutique et au DC, sans vente, hors nouveau SKU converti."""
    if new_sku or has_sales:
        return False
    return expected == 0 and available == 0 and incoming == 0 and not dc_available


def inactive_candidates(
    stock: Iterable[StockSnapshotLine],
    movements: Iterable[StockMovement],
    dc_mapping: Mapping[str, str],
) -> dict[str, list[str]]:
    """Par boutique, les SKU inactifs d'après les deux exports du jour (compteur qualité).

    Les exceptions (événements, conversions) ne sont pas connues ici : ce sont des candidats,
    la table de commande applique les exceptions.
    """
    lines = list(stock)
    dc_lines = {(s.location, s.sku): s for s in lines}
    moved: set[tuple[str, str]] = {(m.location, m.sku) for m in movements}
    out: dict[str, list[str]] = defaultdict(list)
    for s in lines:
        dc = dc_mapping.get(s.location)
        if dc is None:  # pas une boutique connue (DC, emplacement inconnu)
            continue
        dc_line = dc_lines.get((dc, s.sku))
        if is_inactive(
            expected=s.expected,
            available=s.available,
            incoming=s.incoming,
            has_sales=(s.location, s.sku) in moved,
            dc_available=dc_line.available if dc_line else None,
        ):
            out[s.location].append(s.sku)
    return dict(out)
