"""Application des overrides du planner. Fonction pure.

Règle 6 : un override garde un auteur et un horodatage (validés par le modèle `Override`).
Rien n'est demandé au planner : le contexte est déduit de la ligne (`deduce_context`, D-038)
et ne sert qu'à l'Historique et au panneau « Pourquoi », jamais à la table. La quantité
d'origine reste visible dans `forecast_qty` et dans l'explication.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fractions import Fraction

from app.domain.models import (
    OVERRIDE_CONTEXT_LABELS,
    Override,
    OverrideContext,
    QtySource,
    RecommendationLine,
    Status,
)
from app.engines.forecast import ceiling_to_multiple


class OverrideError(ValueError):
    """Override incohérent avec la ligne (SKU inconnu, quantité de départ différente)."""


def deduce_context(line: RecommendationLine, qty_after: int) -> tuple[OverrideContext, str]:
    """Contexte probable d'une modification, lu sur la ligne avant modification (D-038).

    Priorité : événement actif sur le SKU > stock DC insuffisant pour la quantité saisie >
    SKU dormant > « non précisé ». Le détail cite tout ce qui a été trouvé.
    """
    e = line.explanation
    inp = e.get("inputs") or {}
    found: list[tuple[OverrideContext, str]] = []
    applied = (e.get("events") or {}).get("applied") or []
    if applied:
        titles = ", ".join(dict.fromkeys(f"« {t['title']} »" for t in applied))
        found.append((OverrideContext.EVENT, f"Événement actif : {titles}"))
    multiple = int(e.get("multiple") or 1)
    qty_after = ceiling_to_multiple(Fraction(qty_after), multiple if multiple > 0 else 1)
    dc = inp.get("dc_available")
    if qty_after > 0 and (dc is None or dc < qty_after):
        detail = "SKU absent du stock DC" if dc is None else f"{dc:g} < {qty_after}"
        found.append((OverrideContext.DC_SHORT, f"Stock DC insuffisant ({detail})"))
    rules = e.get("rules_triggered") or []
    if (
        "dc_return" in rules
        or "dc_out_of_stock" in rules
        or (inp.get("sales_total") == 0 and inp.get("expected_total") == 0)
    ):
        found.append((OverrideContext.DORMANT, "SKU dormant (ni stock ni ventes)"))
    if not found:
        return OverrideContext.UNSPECIFIED, OVERRIDE_CONTEXT_LABELS[OverrideContext.UNSPECIFIED]
    return found[0][0], " · ".join(text for _, text in found)


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
        # Le contexte déduit n'entre pas dans les raisons (affichées dans la table) : il reste
        # dans l'explication, lue par « Pourquoi » et l'Historique (D-038).
        why = ov.reason.strip()
        reasons = [
            *line.reasons,
            f"Modifié par {ov.author} : {ov.qty_before} → {final}" + (f" ({why})" if why else ""),
        ]
        if final != ov.qty_after:
            reasons.append(f"⚠ Saisie {ov.qty_after} arrondie au multiple de {multiple} : {final}")
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
