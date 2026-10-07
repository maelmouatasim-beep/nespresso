"""Choix des SKU affichés dans une commande. Fonctions pures.

Deux règles :
- `excel` (mode parité) : règle « Filter out » d'Excel. Une ligne est retirée si le SKU
  est dans le Boutique Portfolio avec `Yes`, ou s'il est absent du portfolio et que
  `expected + ventes == 0`. Le portfolio est comparé en numérique puis en texte (A4).
- `standard` (mode de travail, A5) : TOUS les SKU qui ont une activité — dans le
  portfolio boutique, OU Expected / Available / Incoming non nul, OU un mouvement sur
  la fenêtre d'historique. Rien n'est caché : les lignes sans besoin sont à 0 avec
  leur raison.
Chaque SKU écarté l'est avec sa raison, jamais en silence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.models import StockSnapshotLine
from app.engines.conversion import ConversionIndex
from app.engines.portfolio import PortfolioIndex
from app.engines.sales import SalesSummary

RULE_VERSION = "selection-2.0.0"


@dataclass(frozen=True)
class SelectionResult:
    skus: list[str]
    excluded: dict[str, str]


def _activity(
    sku: str,
    boutique_stock: Mapping[str, StockSnapshotLine],
    sales: SalesSummary,
    conversions: ConversionIndex,
) -> float:
    snap = boutique_stock.get(sku)
    total = (snap.expected if snap else 0.0) + sales.sales(sku)
    for old in conversions.by_new.get(sku, ()):
        old_snap = boutique_stock.get(old.old_sku)
        total += (old_snap.expected if old_snap else 0.0) + sales.sales(old.old_sku)
    return total


def select_order_skus(
    boutique_stock: Mapping[str, StockSnapshotLine],
    sales: SalesSummary,
    portfolio: PortfolioIndex,
    conversions: ConversionIndex,
    forced_skus: Iterable[str] = (),
) -> SelectionResult:
    """Règle Excel « Filter out » (mode parité). `forced_skus` sont toujours retenus."""
    forced = list(dict.fromkeys(forced_skus))
    candidates = list(dict.fromkeys([*boutique_stock, *sales.by_sku, *forced]))
    kept: list[str] = []
    excluded: dict[str, str] = {}
    forced_set = set(forced)
    for sku in candidates:
        if sku in forced_set:
            kept.append(sku)
        elif portfolio.filtered_out(sku):
            excluded[sku] = "Boutique Portfolio : Filter out = Yes"
        elif (
            not portfolio.contains(sku) and _activity(sku, boutique_stock, sales, conversions) == 0
        ):
            excluded[sku] = "Hors portfolio, sans stock ni ventes"
        else:
            kept.append(sku)
    return SelectionResult(skus=kept, excluded=excluded)


def select_active_skus(
    boutique_stock: Mapping[str, StockSnapshotLine],
    sales: SalesSummary,
    portfolio: PortfolioIndex,
    known_skus: Iterable[str],
    forced_skus: Iterable[str] = (),
) -> SelectionResult:
    """Périmètre du mode standard (A5) : tout SKU avec une activité."""
    stock_active = [
        sku
        for sku, s in boutique_stock.items()
        if s.expected != 0 or s.available != 0 or s.incoming != 0
    ]
    moved = list(sales.by_sku)
    in_portfolio = portfolio.canonical_skus([*boutique_stock, *moved, *known_skus])
    kept = list(dict.fromkeys([*in_portfolio, *stock_active, *moved, *forced_skus]))
    kept_set = set(kept)
    excluded = {
        sku: "Aucune activité (hors portfolio, stock nul, aucun mouvement)"
        for sku in boutique_stock
        if sku not in kept_set
    }
    return SelectionResult(skus=kept, excluded=excluded)
