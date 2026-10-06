"""Moteur de quantités « Suggested Qty ». Fonctions PURES : aucune I/O, aucun LLM.

Deux modes :
- `excel_parity` : reproduit le calculateur Excel « B80 105.xlsm » (onglet Stock
  Cover Final). Un multiple absent vaut 1, comme dans Excel.
- `safe` : même calcul, mais un café sans multiple reçoit le multiple de sa
  famille (VER-, ORI-) au lieu de 1.

Dans les deux modes, toute donnée manquante ou suspecte est signalée (REVIEW ou
BLOCKED + raison) : le mode ne change que la quantité, jamais la transparence.
Les calculs se font en fractions exactes pour que l'arrondi au multiple ne
dépende pas d'erreurs d'arrondi des nombres à virgule.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from app.domain.models import (
    COFFEE_TYPE,
    ForecastMode,
    PlanningParameters,
    Product,
    RecommendationLine,
    Status,
    StockSnapshotLine,
)
from app.engines.conversion import ConversionIndex
from app.engines.sales import SalesSummary

RULE_VERSION = "forecast-1.0.0"

# Exception codée en dur dans la formule Excel (raison inconnue, cf. open_questions).
EXCEL_HARDCODED_ZERO_SKUS: frozenset[str] = frozenset({"7010.70"})

# Patterns métier confirmés par le planner (CLAUDE.md).
VERTUO_MULTIPLE = 240
VERTUO_ESPRESSO_MULTIPLE = 360
ORIGINAL_MULTIPLE = 800
VERTUO_360_NAMES = ("altissio", "voltesso", "diavolitto", "orafio")

REASON_INFERRED_MULTIPLE = "Multiple inféré depuis la famille, à confirmer"
REASON_MISSING_MULTIPLE = "Multiple manquant"
REASON_SUSPECT_MULTIPLE = "Multiple suspect"
REASON_NO_ACTIVITY = "Aucune activité"
REASON_NEGATIVE_EXPECTED = "Expected négatif"
REASON_DC_INSUFFICIENT = "Stock DC insuffisant"


@dataclass(frozen=True)
class ForecastInputs:
    """Toutes les données nécessaires au calcul, déjà chargées et validées."""

    boutique_stock: Mapping[str, StockSnapshotLine]
    dc_stock: Mapping[str, StockSnapshotLine]
    sales: SalesSummary
    products: Mapping[str, Product]
    conversions: ConversionIndex
    hardcoded_zero_skus: frozenset[str] = EXCEL_HARDCODED_ZERO_SKUS
    sources: Mapping[str, str] = field(default_factory=dict)


def ceiling_to_multiple(raw: Fraction, multiple: int) -> int:
    """Équivalent de Excel `CEILING(raw, multiple)` pour raw > 0 ; 0 sinon."""
    if multiple <= 0:
        raise ValueError(f"multiple doit être > 0, reçu {multiple}")
    if raw <= 0:
        return 0
    return math.ceil(raw / multiple) * multiple


def infer_coffee_multiple(description: str | None) -> int | None:
    """Multiple standard d'un café d'après sa description (VER- / ORI-), sinon None."""
    if not description:
        return None
    desc = description.strip().upper()
    if desc.startswith("VER-"):
        if any(name in desc.lower() for name in VERTUO_360_NAMES):
            return VERTUO_ESPRESSO_MULTIPLE
        return VERTUO_MULTIPLE
    if desc.startswith("ORI-"):
        return ORIGINAL_MULTIPLE
    return None


class _Flags:
    """Accumule statut, raisons et règles déclenchées pour une ligne."""

    _RANK = {Status.OK: 0, Status.REVIEW: 1, Status.BLOCKED: 2}

    def __init__(self) -> None:
        self.status = Status.OK
        self.reasons: list[str] = []
        self.rules: list[str] = []

    def raise_to(self, status: Status, reason: str) -> None:
        if self._RANK[status] > self._RANK[self.status]:
            self.status = status
        self.reasons.append(reason)


def _cover_days(expected: Fraction, sales: Fraction, history_days: int) -> float | None:
    if sales == 0:
        return None
    return float(expected / sales * history_days)


def compute_line(
    sku: str,
    inputs: ForecastInputs,
    params: PlanningParameters,
    mode: ForecastMode,
) -> RecommendationLine:
    """Calcule la quantité suggérée d'un SKU, avec statut et explication complète."""
    history_days = inputs.sales.history_days
    if history_days <= 0:
        raise ValueError("Aucune date de vente : impossible de calculer sales_history_days")

    flags = _Flags()
    product = inputs.products.get(sku)
    snap = inputs.boutique_stock.get(sku)

    description = (snap.description if snap else None) or (product.description if product else None)
    product_type = (snap.product_type if snap else None) or (
        product.product_type if product else None
    )
    is_coffee = product_type == COFFEE_TYPE
    if product_type is None:
        flags.raise_to(Status.REVIEW, "Type produit inconnu")

    # --- Ventes et Expected (avec agrégation des old SKU convertis) ---
    own_sales = inputs.sales.sales(sku)
    if snap is None:
        own_expected = 0.0
        flags.raise_to(
            Status.REVIEW, "SKU absent de la Stock Situation boutique : Expected pris à 0"
        )
    else:
        own_expected = snap.expected

    conv = inputs.conversions.status(sku, params.run_date)
    sales_from_old: dict[str, float] = {}
    expected_from_old: dict[str, float] = {}
    for old in conv.olds_aggregated:
        old_snap = inputs.boutique_stock.get(old.old_sku)
        sales_from_old[old.old_sku] = inputs.sales.sales(old.old_sku)
        expected_from_old[old.old_sku] = old_snap.expected if old_snap else 0.0
        flags.rules.append(f"conversion_new_sku: ventes et Expected de {old.old_sku} ajoutés")

    sales_total = Fraction(own_sales) + sum(map(Fraction, sales_from_old.values()), Fraction(0))
    expected_total = Fraction(own_expected) + sum(
        map(Fraction, expected_from_old.values()), Fraction(0)
    )

    # --- Multiple ---
    list_multiple = product.order_multiple if product else None
    qty_reliable = True
    force_zero_multiple = False
    if list_multiple == 0:
        flags.raise_to(Status.REVIEW, "Multiple nul (0) dans la Multiple list")
        if mode is ForecastMode.EXCEL_PARITY:
            # Excel : CEILING(x; 0) renvoie 0.
            force_zero_multiple = True
        list_multiple = None
    if force_zero_multiple:
        multiple, multiple_source = 0, "excel_zero_multiple"
        qty_reliable = False
    elif list_multiple is not None:
        multiple, multiple_source = list_multiple, "multiple_list"
        if is_coffee and list_multiple == 1:
            flags.raise_to(Status.REVIEW, f"{REASON_SUSPECT_MULTIPLE} : café avec multiple 1")
    else:
        inferred = infer_coffee_multiple(description) if is_coffee else None
        if mode is ForecastMode.SAFE and inferred is not None:
            multiple, multiple_source = inferred, "inferred_family"
            flags.raise_to(Status.REVIEW, f"{REASON_INFERRED_MULTIPLE} ({inferred})")
        else:
            multiple = 1
            qty_reliable = False
            if mode is ForecastMode.EXCEL_PARITY:
                multiple_source = "excel_default_1"
                detail = "1 utilisé comme dans Excel"
            else:
                multiple_source = "missing_default_1"
                detail = "calcul fait avec 1, quantité non fiable"
            flags.raise_to(Status.REVIEW, f"{REASON_MISSING_MULTIPLE} : {detail}")

    # --- Cover appliqué ---
    if params.coffee_cover_days is not None and is_coffee:
        cover, cover_source = params.coffee_cover_days, "coffee_cover_days"
    else:
        cover, cover_source = params.cover_days, "cover_days"

    # --- Quantité ---
    raw: Fraction | None = None
    if sku in inputs.hardcoded_zero_skus:
        qty = 0
        flags.rules.append("excel_hardcoded_zero")
        flags.raise_to(
            Status.BLOCKED,
            f"Exception Excel codée en dur : {sku} toujours à 0 (raison à confirmer)",
        )
    elif conv.old_blocked and conv.as_old is not None:
        qty = 0
        old = conv.as_old
        flags.rules.append("old_sku_blocked")
        if old.has_replacement:
            msg = (
                f"Ancien SKU converti le {old.effective_date.isoformat()} : commander {old.new_sku}"
            )
        else:
            msg = (
                f"Ancien SKU sans remplaçant (XYZ123) depuis le {old.effective_date.isoformat()} : "
                "ne plus envoyer"
            )
        flags.raise_to(Status.BLOCKED, msg)
    else:
        raw = Fraction(cover) * sales_total / history_days - expected_total
        qty = 0 if force_zero_multiple else ceiling_to_multiple(raw, multiple)
        flags.rules.append("ceiling_to_multiple" if raw > 0 else "need_not_positive")
        if conv.as_old is not None:
            old = conv.as_old
            flags.raise_to(
                Status.REVIEW,
                f"Conversion prévue le {old.effective_date.isoformat()} vers {old.new_sku} "
                "(ancien SKU encore commandable)",
            )

    if sales_total == 0 and expected_total == 0:
        flags.reasons.append(REASON_NO_ACTIVITY)
    if expected_total < 0:
        flags.raise_to(Status.REVIEW, f"{REASON_NEGATIVE_EXPECTED} ({float(expected_total):g})")

    # --- Stock DC (signalé, jamais plafonné) ---
    dc_line = inputs.dc_stock.get(sku)
    dc_available = dc_line.available if dc_line else None
    if qty > 0:
        if dc_available is None:
            flags.raise_to(Status.REVIEW, f"{REASON_DC_INSUFFICIENT} : SKU absent du stock DC")
        elif dc_available < qty:
            flags.raise_to(Status.REVIEW, f"{REASON_DC_INSUFFICIENT} ({dc_available:g} < {qty})")

    explanation: dict[str, Any] = {
        "rule_version": RULE_VERSION,
        "mode": mode.value,
        "sources": dict(inputs.sources),
        "parameters": {
            "boutique": params.boutique,
            "run_date": params.run_date.isoformat(),
            "cover_days": params.cover_days,
            "coffee_cover_days": params.coffee_cover_days,
            "sales_history_days": history_days,
            "movement_codes": (
                "ALL"
                if inputs.sales.movement_codes is None
                else sorted(inputs.sales.movement_codes)
            ),
        },
        "inputs": {
            "sales_own": own_sales,
            "expected_own": own_expected,
            "sales_from_old_skus": sales_from_old,
            "expected_from_old_skus": expected_from_old,
            "sales_total": float(sales_total),
            "expected_total": float(expected_total),
            "dc_available": dc_available,
        },
        "cover_applied": cover,
        "cover_source": cover_source,
        "raw_need": None if raw is None else float(raw),
        "multiple": multiple,
        "multiple_source": multiple_source,
        "qty": qty,
        "current_cover_days": _cover_days(expected_total, sales_total, history_days),
        "post_cover_days": _cover_days(expected_total + qty, sales_total, history_days),
        "rules_triggered": flags.rules,
    }
    return RecommendationLine(
        sku=sku,
        description=description,
        product_type=product_type,
        qty=qty,
        qty_reliable=qty_reliable,
        status=flags.status,
        reasons=flags.reasons,
        explanation=explanation,
    )


def compute_recommendations(
    skus: Iterable[str],
    inputs: ForecastInputs,
    params: PlanningParameters,
    mode: ForecastMode,
) -> list[RecommendationLine]:
    """Calcule une ligne par SKU, dans l'ordre donné."""
    return [compute_line(sku, inputs, params, mode) for sku in skus]
