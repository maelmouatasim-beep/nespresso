"""Rapprochement des SKU entre tables (portfolio, exclusions…). Fonctions pures.

Excel cherche d'abord la valeur NUMÉRIQUE du SKU (RECHERCHEV(CNUM(A2))) puis le texte :
« 7005.7 » dans le portfolio correspond donc à « 7005.70 » dans la Stock Situation.
Le SKU reste un texte partout : la valeur numérique ne sert qu'à comparer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


def numeric_key(sku: str) -> str | None:
    """Clé numérique d'un SKU (« 7005.70 » et « 7005.7 » → « 7005.7 »), None si non numérique."""
    text = sku.strip()
    if not text or not any(ch.isdigit() for ch in text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class PortfolioIndex:
    """Portfolio d'une boutique : SKU → indicateur Filter out (« Yes » / « No »)."""

    by_numeric: Mapping[str, str]
    by_text: Mapping[str, str]
    raw_skus: tuple[str, ...]

    @classmethod
    def build(cls, entries: Mapping[str, str]) -> PortfolioIndex:
        by_numeric: dict[str, str] = {}
        by_text: dict[str, str] = {}
        for sku, flag in entries.items():
            by_text.setdefault(sku.strip(), flag)
            key = numeric_key(sku)
            if key is not None:
                by_numeric.setdefault(key, flag)
        return cls(by_numeric=by_numeric, by_text=by_text, raw_skus=tuple(entries))

    def flag(self, sku: str) -> str | None:
        """Indicateur Filter out du SKU, ou None s'il n'est pas dans le portfolio."""
        key = numeric_key(sku)
        if key is not None and key in self.by_numeric:
            return self.by_numeric[key]
        return self.by_text.get(sku.strip())

    def contains(self, sku: str) -> bool:
        return self.flag(sku) is not None

    def filtered_out(self, sku: str) -> bool:
        return (self.flag(sku) or "").strip().lower() == "yes"

    def canonical_skus(self, known: Iterable[str]) -> list[str]:
        """SKU du portfolio écrits comme dans les autres fichiers (« 7005.7 » → « 7005.70 »)."""
        by_key: dict[str, str] = {}
        known_text = set()
        for s in known:
            known_text.add(s)
            key = numeric_key(s)
            if key is not None:
                by_key.setdefault(key, s)
        out = []
        for raw in self.raw_skus:
            text = raw.strip()
            key = numeric_key(text)
            if text in known_text:
                out.append(text)
            elif key is not None and key in by_key:
                out.append(by_key[key])
            else:
                out.append(text)
        return list(dict.fromkeys(out))


EMPTY_PORTFOLIO = PortfolioIndex(by_numeric={}, by_text={}, raw_skus=())
