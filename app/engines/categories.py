"""Catégories d'affichage du tableau de commande (filtre « Catégorie »).

La catégorie dépend seulement du type produit (product_type). La table de
correspondance est un référentiel modifiable (écran Référentiels) ; cette valeur par
défaut est celle donnée par le planner. Elle ne change aucune quantité.

Distinct des familles de palettes (pallets.py), qui suivent la règle Excel.
"""

from __future__ import annotations

from collections.abc import Mapping

CAFES = "Cafés"
MACHINES = "Machines"
ACCESSORIES = "Accessoires"
CONSUMABLES = "Consommables & sacs"
OTHERS = "Autres"
CATEGORIES = (CAFES, MACHINES, ACCESSORIES, CONSUMABLES, OTHERS)

DEFAULT_CATEGORY_MAP: Mapping[str, str] = {
    "C": CAFES,
    "M": MACHINES,
    **{t: CONSUMABLES for t in ("A", "AP", "D", "FB", "PA", "PM", "MD")},
    **{t: ACCESSORIES for t in ("LC", "CH", "GC", "T", "TX", "F")},
}


def normalize_type(product_type: str | None) -> str:
    return (product_type or "").strip().upper()


def category_of(product_type: str | None, mapping: Mapping[str, str] | None = None) -> str:
    """Catégorie d'un type produit ; « Autres » si le type est absent de la table."""
    table = DEFAULT_CATEGORY_MAP if mapping is None else mapping
    return table.get(normalize_type(product_type), OTHERS)
