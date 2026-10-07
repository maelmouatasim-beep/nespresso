"""Résumé déterministe d'un run (compteurs et anomalies). Fonction pure, aucun LLM."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.models import QtySource, RecommendationLine, Status


@dataclass(frozen=True)
class RunSummary:
    lines_total: int
    lines_ordered: int
    units_total: int
    by_status: dict[str, int]
    by_source: dict[str, int]
    unreliable_ordered: list[str]
    reason_counts: list[tuple[str, int]]


def _reason_key(reason: str) -> str:
    """Regroupe les raisons de même nature (« Stock DC insuffisant (3 < 240) » → …)."""
    for sep in (" (", " : "):
        if sep in reason:
            reason = reason.split(sep, 1)[0]
    return reason


def summarize(lines: Sequence[RecommendationLine]) -> RunSummary:
    ordered = [line for line in lines if line.qty > 0]
    return RunSummary(
        lines_total=len(lines),
        lines_ordered=len(ordered),
        units_total=sum(line.qty for line in ordered),
        by_status={s.value: sum(1 for line in lines if line.status is s) for s in Status},
        by_source={s.value: sum(1 for line in ordered if line.source is s) for s in QtySource},
        unreliable_ordered=[line.sku for line in ordered if not line.qty_reliable],
        reason_counts=Counter(_reason_key(r) for line in lines for r in line.reasons).most_common(),
    )
