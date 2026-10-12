"""Conversion des anciens fichiers Allocations, Lancements et Stocks cibles en événements SKU
(D-042). Fonction pure : aucune lecture ni écriture ici.

Un événement par boutique et par fichier (et par date pour un lancement) : chaque SKU garde
sa quantité exacte, ses vagues, son déjà envoyé ou son min / cible / max. Le calcul de la
commande ne change pas : les mêmes valeurs passent par le même arbitrage métier.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from app.domain.events import EventEffect, EventQty, EventScope, EventSku, EventType, SkuEvent
from app.domain.models import Allocation, Launch, TargetStock

MAX_LEAD_DAYS = 120


def _skus(codes: Iterable[str]) -> tuple[EventSku, ...]:
    return tuple(EventSku(sku=c) for c in dict.fromkeys(codes))


def events_from_business(
    allocations: Iterable[Allocation],
    launches: Iterable[Launch],
    targets: Iterable[TargetStock],
    *,
    author: str,
    today: date,
    source: str = "",
) -> list[SkuEvent]:
    """Événements Équipe, actifs, équivalents aux lignes des anciens fichiers.

    Allocation → Promotion « Allocation officielle (vagues) », sans date de fin (comme avant).
    Lancement → Lancement « Quantité initiale − Expected », début et fin = date de lancement,
    anticipation couvrant aujourd'hui (comme avant : appliqué jusqu'à la date de lancement).
    Stock cible → Stock cible « Min / cible / max », sans date de fin.
    """
    common = {"scope": EventScope.TEAM, "owner": author, "planner": author,
              "created_by": author, "source_file": source}  # fmt: skip
    out: list[SkuEvent] = []
    by_btq: dict[str, list[Allocation]] = defaultdict(list)
    for a in allocations:
        by_btq[a.boutique].append(a)
    for b, items in sorted(by_btq.items()):
        comments = sorted({a.comment for a in items if a.comment})
        out.append(SkuEvent(
            type=EventType.PROMOTION, effect=EventEffect.ALLOCATION,
            title=f"Allocations {b} (reprise)", description=" ; ".join(comments),
            skus=_skus(a.sku for a in items), boutiques=(b,), start=today, end=None,
            quantities=tuple(EventQty(boutique=b, sku=a.sku, qty=a.total_qty,
                                      already_sent=a.already_sent, waves=a.wave_plan,
                                      current_wave=a.current_wave) for a in items),
            **common,
        ))  # fmt: skip
    by_launch: dict[tuple[str, date], list[Launch]] = defaultdict(list)
    for la in launches:
        by_launch[(la.boutique, la.launch_date)].append(la)
    for (b, day), launch_items in sorted(by_launch.items()):
        comments = sorted({la.comment for la in launch_items if la.comment})
        lead = min(MAX_LEAD_DAYS, max(7, (day - today).days))
        out.append(SkuEvent(
            type=EventType.LAUNCH, effect=EventEffect.INITIAL_QTY,
            title=f"Lancement {b} {day:%d/%m/%Y} (reprise)", description=" ; ".join(comments),
            skus=_skus(la.sku for la in launch_items), boutiques=(b,), start=day, end=day,
            lead_days=lead,
            quantities=tuple(EventQty(boutique=b, sku=la.sku, qty=la.qty)
                             for la in launch_items),
            **common,
        ))  # fmt: skip
    by_target: dict[str, list[TargetStock]] = defaultdict(list)
    for t in targets:
        if any(v != round(v) for v in (t.min_qty, t.target_qty, t.max_qty)):
            raise ValueError(f"Stock cible {t.boutique} / {t.sku} : min, cible et max doivent "
                             "être des nombres entiers")  # fmt: skip
        by_target[t.boutique].append(t)
    for b, target_items in sorted(by_target.items()):
        out.append(SkuEvent(
            type=EventType.TARGET_STOCK, effect=EventEffect.TARGET,
            title=f"Stocks cibles {b} (reprise)", skus=_skus(t.sku for t in target_items),
            boutiques=(b,), start=today, end=None,
            quantities=tuple(EventQty(boutique=b, sku=t.sku, qty=round(t.target_qty),
                                      min_qty=round(t.min_qty), max_qty=round(t.max_qty))
                             for t in target_items),
            **common,
        ))  # fmt: skip
    return out
