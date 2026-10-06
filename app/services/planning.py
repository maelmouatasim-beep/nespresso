"""Orchestration d'un run de planification. Aucune I/O : tout arrive déjà chargé.

Étapes : sélection des lignes → prévision → arbitrage métier → overrides →
palettes et résumé.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

from app.domain.models import (
    Allocation,
    ForecastMode,
    Launch,
    Override,
    PlanningParameters,
    Product,
    RecommendationLine,
    TargetStock,
)
from app.engines import business, forecast, selection
from app.engines.anomalies import RunSummary, summarize
from app.engines.forecast import ForecastInputs, compute_recommendations
from app.engines.overrides import apply_overrides
from app.engines.pallets import PalletEstimate, estimate_pallets

RULE_VERSIONS = {
    "forecast": forecast.RULE_VERSION,
    "selection": selection.RULE_VERSION,
    "business": business.RULE_VERSION,
}


@dataclass(frozen=True)
class BusinessInputs:
    allocations: tuple[Allocation, ...] = ()
    launches: tuple[Launch, ...] = ()
    targets: tuple[TargetStock, ...] = ()


@dataclass(frozen=True)
class PlanningResult:
    params: PlanningParameters
    mode: ForecastMode
    dc: str
    lines: list[RecommendationLine]
    excluded: dict[str, str]
    pallets: PalletEstimate
    summary: RunSummary
    rule_versions: dict[str, str] = field(default_factory=lambda: dict(RULE_VERSIONS))


def run_planning(
    inputs: ForecastInputs,
    params: PlanningParameters,
    mode: ForecastMode,
    *,
    dc: str,
    portfolio: Mapping[str, str],
    business_inputs: BusinessInputs | None = None,
    overrides: Iterable[Override] = (),
    skus: list[str] | None = None,
) -> PlanningResult:
    """Calcule la commande complète d'une boutique.

    `skus` permet d'imposer la liste des lignes (ex. parité avec un golden) ; sinon la
    règle « Filter out » choisit les lignes.
    """
    business_inputs = business_inputs or BusinessInputs()
    b = params.boutique
    forced = [
        x.sku for x in (*business_inputs.allocations, *business_inputs.launches) if x.boutique == b
    ]
    if skus is None:
        sel = selection.select_order_skus(
            inputs.boutique_stock, inputs.sales, portfolio, inputs.conversions, forced
        )
        skus, excluded = sel.skus, sel.excluded
    else:
        skus = list(dict.fromkeys([*skus, *forced]))
        excluded = {}

    lines = compute_recommendations(skus, inputs, params, mode)
    lines = business.apply_business_rules(
        lines,
        boutique=b,
        run_date=params.run_date,
        allocations=business_inputs.allocations,
        launches=business_inputs.launches,
        targets=business_inputs.targets,
    )
    lines = apply_overrides(lines, overrides)
    return PlanningResult(
        params=params,
        mode=mode,
        dc=dc,
        lines=lines,
        excluded=excluded,
        pallets=estimate_pallets(lines, inputs.products, dc),
        summary=summarize(lines),
    )


def with_overrides(
    result: PlanningResult, overrides: Iterable[Override], products: Mapping[str, Product]
) -> PlanningResult:
    """Applique des overrides à un résultat SANS override et recalcule palettes et résumé."""
    lines = apply_overrides(result.lines, overrides)
    return replace(
        result,
        lines=lines,
        pallets=estimate_pallets(lines, products, result.dc),
        summary=summarize(lines),
    )
