"""Contrôle des règles boutique validées par un humain. Fonction pure.

Une règle ne change jamais une quantité : une règle SKU passe la ligne en REVIEW,
une règle de commande (palettes, unités) produit une alerte de run.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.domain.models import BoutiqueRule, BoutiqueRuleKind, RecommendationLine, Status
from app.engines.pallets import PalletEstimate


def check_boutique_rules(
    lines: Sequence[RecommendationLine],
    rules: Iterable[BoutiqueRule],
    boutique: str,
    pallets: PalletEstimate,
) -> tuple[list[RecommendationLine], list[str]]:
    mine = [r for r in rules if r.boutique == boutique]
    by_sku: dict[str, list[BoutiqueRule]] = {}
    for r in mine:
        if r.sku is not None:
            by_sku.setdefault(r.sku, []).append(r)
    out: list[RecommendationLine] = []
    for line in lines:
        flags = []
        for r in by_sku.get(line.sku, []):
            note = f" ({r.comment})" if r.comment else ""
            if r.rule is BoutiqueRuleKind.MAX_QTY_SKU and line.qty > r.value:
                flags.append(
                    f"Règle boutique : max {r.value:g} pour ce SKU, commande {line.qty}{note}"
                )
            if (
                r.rule is BoutiqueRuleKind.MIN_QTY_SKU
                and line.qty < r.value
                and line.status is not Status.BLOCKED
            ):
                flags.append(
                    f"Règle boutique : min {r.value:g} pour ce SKU, commande {line.qty}{note}"
                )
        if flags:
            status = Status.REVIEW if line.status is Status.OK else line.status
            line = line.model_copy(update={"status": status, "reasons": [*line.reasons, *flags]})
        out.append(line)

    warnings: list[str] = []
    units = sum(x.qty for x in out if x.qty > 0)
    for r in mine:
        note = f" ({r.comment})" if r.comment else ""
        if r.rule is BoutiqueRuleKind.MAX_PALLETS and pallets.total_pallets > r.value:
            total = pallets.total_pallets
            warnings.append(
                f"Règle boutique : {total:.2f} palettes estimées > max {r.value:g}{note}"
            )
        if r.rule is BoutiqueRuleKind.MAX_UNITS and units > r.value:
            warnings.append(f"Règle boutique : {units} unités > max {r.value:g}{note}")
    return out, warnings
