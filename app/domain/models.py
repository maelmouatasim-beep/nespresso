"""Modèles métier (pydantic v2).

Règle d'or : un SKU est TOUJOURS une chaîne de caractères ("7005.70", "0005452",
"3517/BULK"). Le type `Sku` refuse les nombres pour qu'aucune conversion ne passe
inaperçue.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints

Sku = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1)]

COFFEE_TYPE = "C"
NO_REPLACEMENT_SKU = "XYZ123"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Status(StrEnum):
    OK = "OK"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"


class ForecastMode(StrEnum):
    EXCEL_PARITY = "excel_parity"
    SAFE = "safe"


class Product(_Frozen):
    """Une ligne de la Multiple list."""

    sku: Sku
    description: str | None = None
    product_type: str | None = None
    order_multiple: int | None = Field(default=None, ge=0)  # 0 existe dans la liste réelle
    units_per_pallet: float | None = None


class StockSnapshotLine(_Frozen):
    """Une ligne de l'export Power BI Stock Situation (boutique ou DC)."""

    location: str
    sku: Sku
    description: str | None = None
    product_type: str | None = None
    expected: float
    available: float
    incoming: float
    reserved: float
    waiting: float


class StockMovement(_Frozen):
    """Un mouvement de stock sortant (export Power BI Stock Movements)."""

    movement_id: str
    location: str
    sku: Sku
    product_type: str | None = None
    movement_date: date
    movement_code: str
    quantity: float
    movement_description: str | None = None


class SkuConversion(_Frozen):
    """Old SKU -> new SKU. new_sku == "XYZ123" signifie « pas de remplaçant »."""

    old_sku: Sku
    new_sku: Sku
    effective_date: date
    comment: str | None = None

    @property
    def has_replacement(self) -> bool:
        return self.new_sku != NO_REPLACEMENT_SKU


class PlanningParameters(_Frozen):
    """Paramètres saisis par le planner à CHAQUE run.

    Aucun champ n'a de valeur par défaut : `coffee_cover_days` doit être passé
    explicitement (un nombre, ou None pour « pas de cover café spécifique »).
    Les cover days ne sont jamais lus depuis le Schedule ni la Bible.
    """

    boutique: str = Field(min_length=1)
    run_date: date
    cover_days: float = Field(gt=0)
    coffee_cover_days: float | None = Field(gt=0)


class RecommendationLine(_Frozen):
    """Résultat du moteur pour un SKU : quantité + statut + trace complète."""

    sku: Sku
    description: str | None
    product_type: str | None
    qty: int = Field(ge=0)
    qty_reliable: bool
    status: Status
    reasons: list[str]
    explanation: dict[str, Any]
