"""Comparaison de deux commandes d'une même boutique. Fonction pure."""

from __future__ import annotations

from collections.abc import Mapping


def compare_orders(previous: Mapping[str, int], current: Mapping[str, int]) -> list[dict]:
    """Lignes où la quantité change entre deux commandes (SKU, avant, maintenant, écart)."""
    rows = []
    for sku in sorted(set(previous) | set(current)):
        before, now = previous.get(sku, 0), current.get(sku, 0)
        if before != now:
            rows.append({"sku": sku, "avant": before, "maintenant": now, "écart": now - before})
    return rows
