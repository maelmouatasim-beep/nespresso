"""Familles d'affichage du tableau de commande (ordre imposé par le planner).

Distinct des familles de palettes (pallets.py), qui suivent la règle Excel.
"""

from __future__ import annotations

from app.domain.models import COFFEE_TYPE, RecommendationLine

VERTUO = "Cafés Vertuo"
ORIGINAL = "Cafés Original"
MACHINES = "Machines"
ACCESSORIES = "Accessoires"
CONSUMABLES = "Consommables et sacs"
OTHERS = "Autres"
DISPLAY_ORDER = (VERTUO, ORIGINAL, MACHINES, ACCESSORIES, CONSUMABLES, OTHERS)

_ACCESSORY_TYPES = frozenset({"LC", "AP", "MD", "GC", "CH"})
_CONSUMABLE_TYPES = frozenset({"A", "F", "FB", "PM", "PA"})


def display_family(description: str | None, product_type: str | None) -> str:
    desc = (description or "").strip().upper()
    if product_type == COFFEE_TYPE and desc.startswith("VER-"):
        return VERTUO
    if product_type == COFFEE_TYPE and desc.startswith("ORI-"):
        return ORIGINAL
    if product_type == "M":
        return MACHINES
    if product_type in _ACCESSORY_TYPES:
        return ACCESSORIES
    if product_type in _CONSUMABLE_TYPES:
        return CONSUMABLES
    return OTHERS


def line_family(line: RecommendationLine) -> str:
    return display_family(line.description, line.product_type)
