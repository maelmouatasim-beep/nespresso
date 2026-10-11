"""Arbitrage entre prévision, allocations, lancements et stock cible. Fonctions pures.

Toutes les quantités sont arrondies au multiple supérieur (décision A3), sauf le
plafond du stock cible, arrondi au multiple inférieur pour ne pas dépasser le max.

Hiérarchie appliquée (hypothèse documentée, open_questions Q-013) :
1. Allocation officielle : REMPLACE la prévision (jamais écrasée par elle, règle 5).
2. Lancement : avant la date de lancement, la quantité initiale remplace la prévision
   (moins le stock déjà attendu, pour ne pas compter deux fois).
3. Stock cible : borne la prévision (min → remonte à la cible, max → plafonne).
Une ligne ne reçoit qu'UNE source de quantité : pas de double comptage.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date
from fractions import Fraction
from typing import Any

from app.domain.models import (
    Allocation,
    Launch,
    QtySource,
    RecommendationLine,
    Status,
    TargetStock,
)
from app.engines.forecast import ceiling_to_multiple

RULE_VERSION = "business-2.0.0"


class BusinessInputError(ValueError):
    """Entrées métier incohérentes (doublons…)."""


def _index(items: Iterable[Any], boutique: str, kind: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if item.boutique != boutique:
            continue
        if item.sku in out:
            raise BusinessInputError(f"{kind} en double pour {boutique} / {item.sku}")
        out[item.sku] = item
    return out


def allocation_due(alloc: Allocation) -> tuple[int, int]:
    """(quantité due, % cumulé). Arrondi au plus proche, 0,5 vers le haut."""
    cumulative = sum(alloc.wave_plan[: alloc.current_wave])
    planned = (alloc.total_qty * cumulative + 50) // 100
    return max(0, planned - alloc.already_sent), cumulative


def _update(
    line: RecommendationLine,
    *,
    qty: int,
    source: QtySource,
    reasons: Sequence[str],
    status: Status | None = None,
    trace: dict[str, Any],
) -> RecommendationLine:
    explanation = dict(line.explanation)
    explanation["business"] = {"rule_version": RULE_VERSION, **trace}
    return line.model_copy(
        update={
            "qty": qty,
            "source": source,
            "forecast_qty": line.qty if line.forecast_qty is None else line.forecast_qty,
            "reasons": [*line.reasons, *reasons],
            "status": status or line.status,
            "explanation": explanation,
        }
    )


def _multiple(line: RecommendationLine) -> int:
    m = int(line.explanation.get("multiple") or 1)
    return m if m > 0 else 1


def apply_business_rules(
    lines: Sequence[RecommendationLine],
    *,
    boutique: str,
    run_date: date,
    allocations: Iterable[Allocation] = (),
    launches: Iterable[Launch] = (),
    targets: Iterable[TargetStock] = (),
) -> list[RecommendationLine]:
    allocs = _index(allocations, boutique, "Allocation")
    launch_by_sku = _index(launches, boutique, "Lancement")
    target_by_sku = _index(targets, boutique, "Stock cible")
    out: list[RecommendationLine] = []
    for line in lines:
        expected = float(line.explanation["inputs"]["expected_total"])
        multiple = _multiple(line)
        alloc = allocs.get(line.sku)
        launch = launch_by_sku.get(line.sku)
        target = target_by_sku.get(line.sku)

        if alloc is not None:
            due, cum = allocation_due(alloc)
            reasons = [
                f"Allocation officielle : vague {alloc.current_wave}/{len(alloc.wave_plan)} "
                f"({cum} % cumulés de {alloc.total_qty}, déjà envoyé {alloc.already_sent}) → {due}"
            ]
            status = line.status
            if line.status is Status.BLOCKED:
                status = Status.REVIEW
                reasons.append("Allocation sur un SKU bloqué par la prévision : à vérifier")
            rounded = ceiling_to_multiple(Fraction(due), multiple)
            if rounded != due:
                reasons.append(f"Arrondi au multiple de {multiple} : {due} → {rounded}")
            if launch is not None:
                reasons.append("Lancement ignoré : l'allocation officielle prime")
            line = _update(
                line,
                qty=rounded,
                source=QtySource.ALLOCATION,
                reasons=reasons,
                status=status,
                trace={
                    "allocation": alloc.model_dump(mode="json"),
                    "allocation_due": due,
                    "rounded_to_multiple": rounded,
                },
            )
        elif launch is not None and run_date <= launch.launch_date:
            need = launch.qty - expected
            qty = ceiling_to_multiple(Fraction(need), multiple)
            status = Status.REVIEW if line.status is Status.OK else line.status
            line = _update(
                line,
                qty=qty,
                source=QtySource.LAUNCH,
                reasons=[
                    f"Lancement le {launch.launch_date.isoformat()} : quantité initiale "
                    f"{launch.qty} − Expected {expected:g} → {qty}"
                ],
                status=status,
                trace={"launch": launch.model_dump(mode="json")},
            )
        elif target is not None and line.status is not Status.BLOCKED:
            projected = expected + line.qty
            if projected < target.min_qty:
                qty = ceiling_to_multiple(Fraction(target.target_qty - expected), multiple)
                line = _update(
                    line,
                    qty=qty,
                    source=QtySource.TARGET_STOCK,
                    reasons=[
                        f"Stock cible : {projected:g} après commande < min {target.min_qty:g}, "
                        f"remonté à la cible {target.target_qty:g} → {qty}"
                    ],
                    trace={"target": target.model_dump(mode="json")},
                )
            elif projected > target.max_qty:
                room = max(0.0, target.max_qty - expected)
                qty = min(line.qty, math.floor(room / multiple) * multiple)
                line = _update(
                    line,
                    qty=qty,
                    source=QtySource.TARGET_STOCK,
                    reasons=[
                        f"Stock cible : {projected:g} après commande > max {target.max_qty:g}, "
                        f"plafonné → {qty}"
                    ],
                    trace={"target": target.model_dump(mode="json")},
                )
        out.append(line)
    return out
