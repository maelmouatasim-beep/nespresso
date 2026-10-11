"""Orchestration d'un run de planification. Aucune I/O : tout arrive déjà chargé.

Étapes : sélection des lignes → prévision → arbitrage métier → événements SKU →
overrides → palettes → règles boutique → résumé.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from app.domain.events import EventEffect, SkuEvent
from app.domain.models import (
    Allocation,
    BoutiqueRule,
    ForecastMode,
    Launch,
    Override,
    PlanningParameters,
    Product,
    RecommendationLine,
    TargetStock,
)
from app.engines import business, events, forecast, selection
from app.engines.anomalies import RunSummary, summarize
from app.engines.forecast import ForecastInputs, compute_recommendations
from app.engines.overrides import apply_overrides
from app.engines.pallets import PalletEstimate, estimate_pallets
from app.engines.portfolio import PortfolioIndex
from app.engines.rules import check_boutique_rules

RULE_VERSIONS = {
    "forecast": forecast.RULE_VERSION,
    "selection": selection.RULE_VERSION,
    "business": business.RULE_VERSION,
    "events": events.RULE_VERSION,
}


@dataclass(frozen=True)
class BusinessInputs:
    allocations: tuple[Allocation, ...] = ()
    launches: tuple[Launch, ...] = ()
    targets: tuple[TargetStock, ...] = ()
    rules: tuple[BoutiqueRule, ...] = ()


@dataclass(frozen=True)
class EventContext:
    """Événements SKU à considérer pour un run : TOUS les événements connus, le planner
    qui calcule, et les événements personnels d'un collègue qu'il a repris."""

    events: tuple[SkuEvent, ...] = ()
    planner: str = ""
    taken_over: frozenset[int] = frozenset()


@dataclass(frozen=True)
class PlanningResult:
    params: PlanningParameters
    mode: ForecastMode
    dc: str
    lines: list[RecommendationLine]  # lignes finales (overrides et règles appliqués)
    excluded: dict[str, str]
    pallets: PalletEstimate
    summary: RunSummary
    base_lines: list[RecommendationLine] = field(default_factory=list)  # avant overrides
    rules: tuple[BoutiqueRule, ...] = ()
    rule_warnings: list[str] = field(default_factory=list)
    history_days: int = 0
    events: tuple[SkuEvent, ...] = ()  # événements appliqués à cette commande
    rule_versions: dict[str, str] = field(default_factory=lambda: dict(RULE_VERSIONS))


def _finalize(
    base: PlanningResult, overrides: Iterable[Override], products: Mapping[str, Product]
) -> PlanningResult:
    lines = apply_overrides(base.base_lines, overrides)
    pallets = estimate_pallets(lines, products, base.dc)
    lines, warnings = check_boutique_rules(lines, base.rules, base.params.boutique, pallets)
    return replace(
        base, lines=lines, pallets=pallets, summary=summarize(lines), rule_warnings=warnings
    )


def run_planning(
    inputs: ForecastInputs,
    params: PlanningParameters,
    mode: ForecastMode,
    *,
    dc: str,
    portfolio: Mapping[str, str] | None = None,
    business_inputs: BusinessInputs | None = None,
    overrides: Iterable[Override] = (),
    skus: list[str] | None = None,
    event_context: EventContext | None = None,
) -> PlanningResult:
    """Calcule la commande complète d'une boutique.

    `skus` permet d'imposer la liste des lignes (ex. parité avec un golden). Sinon :
    mode parité → règle Excel « Filter out » ; mode standard → tous les SKU actifs (A5).
    `portfolio` (facultatif) remplace le portfolio déjà présent dans `inputs`.
    `event_context` : événements SKU ; seuls ceux actifs à la date de livraison, pour la
    boutique et le planner, s'appliquent. Sans événement actif, rien ne change (parité).
    """
    if portfolio is not None:
        inputs = replace(inputs, portfolio=PortfolioIndex.build(portfolio))
    business_inputs = business_inputs or BusinessInputs()
    b = params.boutique
    ctx = event_context or EventContext()
    active = events.select_for_order(
        ctx.events, boutique=b, dc=dc, delivery=params.delivery_date, planner=ctx.planner,
        taken_over=ctx.taken_over,
    )  # fmt: skip
    problems: dict[str, str] = {}
    if any(e.effect is EventEffect.REPLACE for e in active):
        merged = events.merge_conversions(inputs.conversions, active, params.run_date)
        inputs, problems = replace(inputs, conversions=merged.index), merged.problems
    forced = [
        x.sku for x in (*business_inputs.allocations, *business_inputs.launches) if x.boutique == b
    ]
    forced += [s for s in events.forced_skus(active) if s not in forced]
    if skus is None:
        if mode is ForecastMode.EXCEL_PARITY:
            sel = selection.select_order_skus(
                inputs.boutique_stock, inputs.sales, inputs.portfolio, inputs.conversions, forced
            )
        else:
            sel = selection.select_active_skus(
                inputs.boutique_stock, inputs.sales, inputs.portfolio, inputs.products, forced
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
    lines = events.apply_events(
        lines, active, boutique=b, delivery=params.delivery_date, problems=problems
    )
    base = PlanningResult(
        params=params,
        mode=mode,
        dc=dc,
        lines=lines,
        excluded=excluded,
        pallets=estimate_pallets(lines, inputs.products, dc),
        summary=summarize(lines),
        base_lines=lines,
        rules=tuple(r for r in business_inputs.rules if r.boutique == b),
        history_days=inputs.sales.history_days,
        events=tuple(active),
    )
    return _finalize(base, overrides, inputs.products)


def with_overrides(
    result: PlanningResult, overrides: Iterable[Override], products: Mapping[str, Product]
) -> PlanningResult:
    """Réapplique des overrides (et les règles boutique) à partir des lignes avant overrides."""
    return _finalize(result, overrides, products)


# --- Journée : plusieurs boutiques ---------------------------------------------------


@dataclass(frozen=True)
class DayRequest:
    """Ce que le planner saisit pour une boutique de la journée."""

    params: PlanningParameters


@dataclass(frozen=True)
class DayOutcome:
    boutique: str
    result: PlanningResult | None
    inputs: ForecastInputs | None
    error: str | None


def run_day(
    requests: Iterable[PlanningParameters],
    mode: ForecastMode,
    *,
    build_inputs: Callable[[str], tuple[ForecastInputs, str]],
    portfolio_by_boutique: Mapping[str, Mapping[str, str]] | None = None,
    business_inputs: BusinessInputs | None = None,
    event_context: EventContext | None = None,
) -> list[DayOutcome]:
    """Calcule la commande de chaque boutique. Une erreur sur une boutique n'arrête pas
    les autres : elle est retournée avec son message."""
    out: list[DayOutcome] = []
    for params in requests:
        try:
            inputs, dc = build_inputs(params.boutique)
            result = run_planning(
                inputs, params, mode, dc=dc,
                portfolio=(
                    portfolio_by_boutique.get(params.boutique, {})
                    if portfolio_by_boutique is not None
                    else None
                ),
                business_inputs=business_inputs,
                event_context=event_context,
            )  # fmt: skip
            out.append(DayOutcome(params.boutique, result, inputs, None))
        except ValueError as exc:
            out.append(DayOutcome(params.boutique, None, None, str(exc)))
    return out
