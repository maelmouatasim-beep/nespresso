"""Sélection des SKU à afficher dans la commande (règle « Filter out » d'Excel).

Fonction pure. Règle de CLAUDE.md : une ligne est retirée si le SKU est dans le
Boutique Portfolio avec `Yes`, ou s'il est absent du portfolio et que
`expected + ventes == 0`.

⚠️ Cette règle ne reproduit pas exactement la liste du golden B80 (open_questions
Q-002). Chaque exclusion est donc retournée avec sa raison, jamais silencieuse.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.models import StockSnapshotLine
from app.engines.conversion import ConversionIndex
from app.engines.sales import SalesSummary

RULE_VERSION = "selection-1.0.0"


@dataclass(frozen=True)
class SelectionResult:
    skus: list[str]
    excluded: dict[str, str]


def select_order_skus(
    boutique_stock: Mapping[str, StockSnapshotLine],
    sales: SalesSummary,
    portfolio: Mapping[str, str],
    conversions: ConversionIndex,
    forced_skus: Iterable[str] = (),
) -> SelectionResult:
    """Retourne les SKU retenus (ordre stable) et les SKU exclus avec leur raison.

    `forced_skus` (allocations, lancements) sont toujours retenus.
    Les new SKU de conversion sont retenus si leur old SKU a de l'activité.
    """
    forced = list(dict.fromkeys(forced_skus))
    candidates = list(dict.fromkeys([*boutique_stock, *sales.by_sku, *forced]))
    kept: list[str] = []
    excluded: dict[str, str] = {}
    forced_set = set(forced)
    for sku in candidates:
        if sku in forced_set:
            kept.append(sku)
            continue
        flag = (portfolio.get(sku) or "").strip().lower()
        snap = boutique_stock.get(sku)
        activity = (snap.expected if snap else 0.0) + sales.sales(sku)
        for old in conversions.by_new.get(sku, ()):
            old_snap = boutique_stock.get(old.old_sku)
            activity += (old_snap.expected if old_snap else 0.0) + sales.sales(old.old_sku)
        if flag == "yes":
            excluded[sku] = "Boutique Portfolio : Filter out = Yes"
        elif sku not in portfolio and activity == 0:
            excluded[sku] = "Hors portfolio, sans stock ni ventes"
        else:
            kept.append(sku)
    return SelectionResult(skus=kept, excluded=excluded)
