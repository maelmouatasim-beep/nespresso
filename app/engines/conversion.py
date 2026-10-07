"""Résolution des conversions old SKU -> new SKU. Fonctions pures, aucune I/O.

Logique Excel (un seul niveau, pas de chaîne A -> B -> C) :
- le NEW SKU additionne ventes et Expected de son OLD SKU ;
- l'OLD SKU vaut 0 à partir de effective_date + 1 jour ;
- new_sku == "XYZ123" : pas de remplaçant, ne plus envoyer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.models import SkuConversion


class ConversionError(ValueError):
    """Table de conversion incohérente (doublon, chaîne non gérée)."""


@dataclass(frozen=True)
class ConversionStatus:
    """Rôle d'un SKU dans les conversions, à une date de run donnée."""

    as_old: SkuConversion | None
    old_blocked: bool
    olds_aggregated: tuple[SkuConversion, ...]


@dataclass(frozen=True)
class ConversionIndex:
    by_old: dict[str, SkuConversion]
    by_new: dict[str, tuple[SkuConversion, ...]]

    def status(self, sku: str, run_date: date) -> ConversionStatus:
        as_old = self.by_old.get(sku)
        blocked = as_old is not None and is_old_sku_blocked(as_old, run_date)
        return ConversionStatus(
            as_old=as_old, old_blocked=blocked, olds_aggregated=self.by_new.get(sku, ())
        )


def is_old_sku_blocked(conversion: SkuConversion, run_date: date) -> bool:
    """L'ancien SKU est bloqué dès run_date >= effective_date + 1 jour."""
    return run_date >= conversion.effective_date + timedelta(days=1)


def build_conversion_index(conversions: Iterable[SkuConversion]) -> ConversionIndex:
    """Indexe la table. Refuse les cas qu'Excel ne gère pas, au lieu de les deviner."""
    by_old: dict[str, SkuConversion] = {}
    by_new: dict[str, list[SkuConversion]] = {}
    for conv in conversions:
        if conv.old_sku in by_old:
            raise ConversionError(f"Old SKU {conv.old_sku!r} présent deux fois dans SKU Conversion")
        by_old[conv.old_sku] = conv
        if conv.has_replacement:
            by_new.setdefault(conv.new_sku, []).append(conv)
    chained = sorted(set(by_old) & set(by_new))
    if chained:
        raise ConversionError(
            f"Chaînes de conversion A -> B -> C non gérées (SKU à la fois old et new) : {chained}"
        )
    return ConversionIndex(by_old=by_old, by_new={k: tuple(v) for k, v in by_new.items()})
