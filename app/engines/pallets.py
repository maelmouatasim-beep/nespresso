"""Estimation du nombre de palettes (onglet Order Data / Recap d'Excel). Fonction pure.

Règles Excel (CLAUDE.md) :
- cafés Vertuo (VL) : Σ qty / 8000 (8640 si DC = CY1)
- cafés Original (OL) : Σ qty / 24000 (28800 si CY1)
- machines (type M) : Σ qty / 20 (24 si CY1)
- autres : Σ (qty / units_per_pallet)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.domain.models import COFFEE_TYPE, Product, RecommendationLine

VL, OL, MACHINES, OTHERS = "Cafés Vertuo (VL)", "Cafés Original (OL)", "Machines", "Autres"
PER_PALLET = {
    VL: {"default": 8000, "CY1": 8640},
    OL: {"default": 24000, "CY1": 28800},
    MACHINES: {"default": 20, "CY1": 24},
}


@dataclass(frozen=True)
class PalletEstimate:
    units: dict[str, int]
    pallets: dict[str, float]
    total_pallets: float
    skus_without_pallet_size: list[str] = field(default_factory=list)


def family(line: RecommendationLine) -> str:
    desc = (line.description or "").strip().upper()
    if line.product_type == COFFEE_TYPE and desc.startswith("VER-"):
        return VL
    if line.product_type == COFFEE_TYPE and desc.startswith("ORI-"):
        return OL
    if line.product_type == "M":
        return MACHINES
    return OTHERS


def estimate_pallets(
    lines: Iterable[RecommendationLine], products: Mapping[str, Product], dc: str
) -> PalletEstimate:
    units = {VL: 0, OL: 0, MACHINES: 0, OTHERS: 0}
    other_pallets = 0.0
    missing: list[str] = []
    for line in lines:
        if line.qty <= 0:
            continue
        fam = family(line)
        units[fam] += line.qty
        if fam == OTHERS:
            product = products.get(line.sku)
            upp = product.units_per_pallet if product else None
            if upp is None or upp <= 0:
                missing.append(line.sku)
            else:
                other_pallets += line.qty / upp
    pallets = {
        fam: units[fam] / PER_PALLET[fam].get(dc, PER_PALLET[fam]["default"])
        for fam in (VL, OL, MACHINES)
    }
    pallets[OTHERS] = other_pallets
    return PalletEstimate(
        units=units,
        pallets=pallets,
        total_pallets=sum(pallets.values()),
        skus_without_pallet_size=missing,
    )
