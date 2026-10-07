"""Application des overrides du planner. Fonction pure.

Règle 6 : un override exige une raison, un auteur et un horodatage (validés par le
modèle `Override`). La quantité d'origine reste visible dans `forecast_qty` et dans
l'explication.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fractions import Fraction

from app.domain.models import (
    OVERRIDE_CATEGORY_LABELS,
    Override,
    QtySource,
    RecommendationLine,
    Status,
)
from app.engines.forecast import ceiling_to_multiple


class OverrideError(ValueError):
    """Override incohérent avec la ligne (SKU inconnu, quantité de départ différente)."""


def apply_overrides(
    lines: Sequence[RecommendationLine], overrides: Iterable[Override]
) -> list[RecommendationLine]:
    by_sku: dict[str, Override] = {}
    for item in overrides:
        if item.sku in by_sku:
            raise OverrideError(f"Deux overrides pour le SKU {item.sku}")
        by_sku[item.sku] = item
    known = {line.sku for line in lines}
    unknown = sorted(set(by_sku) - known)
    if unknown:
        raise OverrideError(f"Override sur des SKU absents de la commande : {unknown}")

    out: list[RecommendationLine] = []
    for line in lines:
        ov = by_sku.get(line.sku)
        if ov is None:
            out.append(line)
            continue
        if ov.qty_before != line.qty:
            raise OverrideError(
                f"{line.sku} : l'override part de {ov.qty_before} mais la ligne vaut {line.qty} "
                "(données recalculées depuis ?)"
            )
        multiple = int(line.explanation.get("multiple") or 1)
        multiple = multiple if multiple > 0 else 1
        final = ceiling_to_multiple(Fraction(ov.qty_after), multiple)
        status = line.status
        label = OVERRIDE_CATEGORY_LABELS[ov.category]
        reasons = [
            *line.reasons,
            f"Modifié par {ov.author} : {ov.qty_before} → {final} ({label} : {ov.reason.strip()})",
        ]
        if final != ov.qty_after:
            reasons.append(f"Saisie {ov.qty_after} arrondie au multiple de {multiple} : {final}")
        if line.status is Status.BLOCKED and ov.qty_after > 0:
            status = Status.REVIEW
            reasons.append("Override sur une ligne bloquée : à vérifier")
        explanation = dict(line.explanation)
        explanation["override"] = {**ov.model_dump(mode="json"), "qty_final": final}
        out.append(
            line.model_copy(
                update={
                    "qty": final,
                    "source": QtySource.OVERRIDE,
                    "forecast_qty": line.qty if line.forecast_qty is None else line.forecast_qty,
                    "status": status,
                    "reasons": reasons,
                    "explanation": explanation,
                }
            )
        )
    return out
