"""Modèles métier (pydantic v2).

Règle d'or : un SKU est TOUJOURS une chaîne de caractères ("7005.70", "0005452",
"3517/BULK"). Le type `Sku` refuse les nombres pour qu'aucune conversion ne passe
inaperçue.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, model_validator

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
    delivery_date: date
    cover_days: float = Field(gt=0)
    coffee_cover_days: float | None = Field(gt=0)

    @model_validator(mode="after")
    def _delivery_after_run(self) -> PlanningParameters:
        if self.delivery_date < self.run_date:
            raise ValueError("La date de livraison doit être le jour du run ou après")
        return self


class QtySource(StrEnum):
    """D'où vient la quantité finale d'une ligne."""

    FORECAST = "forecast"
    ALLOCATION = "allocation"
    LAUNCH = "launch"
    TARGET_STOCK = "target_stock"
    OVERRIDE = "override"


class RecommendationLine(_Frozen):
    """Résultat du moteur pour un SKU : quantité + statut + trace complète.

    `qty` est la quantité finale. `forecast_qty` garde la quantité de la prévision
    seule, pour que tout ajustement (allocation, lancement, stock cible, override)
    reste visible.
    """

    sku: Sku
    description: str | None
    product_type: str | None
    qty: int = Field(ge=0)
    qty_reliable: bool
    status: Status
    reasons: list[str]
    explanation: dict[str, Any]
    source: QtySource = QtySource.FORECAST
    forecast_qty: int | None = None


# --- Entrées métier saisies par l'équipe (prompt 5) ------------------------------


class Allocation(_Frozen):
    """Quantité imposée par le business, éventuellement envoyée en vagues.

    `wave_plan` = pourcentages par vague (ex. (75, 25)), total 100.
    Quantité due au run = total × % cumulé des vagues jusqu'à `current_wave`
    − déjà envoyé (jamais négative).
    """

    boutique: str = Field(min_length=1)
    sku: Sku
    total_qty: int = Field(ge=0)
    already_sent: int = Field(ge=0)
    wave_plan: tuple[int, ...]
    current_wave: int = Field(ge=1)
    comment: str | None = None

    @model_validator(mode="after")
    def _check_waves(self) -> Allocation:
        if not self.wave_plan or any(w < 0 for w in self.wave_plan):
            raise ValueError("wave_plan doit contenir des pourcentages positifs")
        if sum(self.wave_plan) != 100:
            raise ValueError(f"wave_plan doit totaliser 100 (reçu {sum(self.wave_plan)})")
        if self.current_wave > len(self.wave_plan):
            raise ValueError("current_wave dépasse le nombre de vagues")
        return self


class Launch(_Frozen):
    """Lancement produit : quantité initiale à mettre en boutique avant la date."""

    boutique: str = Field(min_length=1)
    sku: Sku
    launch_date: date
    qty: int = Field(gt=0)
    comment: str | None = None


class TargetStock(_Frozen):
    """Stock min / cible / max d'un SKU en boutique, après commande."""

    boutique: str = Field(min_length=1)
    sku: Sku
    min_qty: float = Field(ge=0)
    target_qty: float = Field(ge=0)
    max_qty: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> TargetStock:
        if not self.min_qty <= self.target_qty <= self.max_qty:
            raise ValueError("Il faut min <= cible <= max")
        return self


class Override(_Frozen):
    """Modification manuelle d'une quantité par un planner (règle 6)."""

    sku: Sku
    qty_before: int = Field(ge=0)
    qty_after: int = Field(ge=0)
    reason: str = Field(min_length=3)
    author: str = Field(min_length=1)
    timestamp: datetime

    @model_validator(mode="after")
    def _strip(self) -> Override:
        if len(self.reason.strip()) < 3:
            raise ValueError("Une raison est obligatoire pour modifier une quantité")
        if not self.author.strip():
            raise ValueError("L'auteur est obligatoire")
        return self
