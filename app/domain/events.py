"""Événements SKU : promotions, lancements, arrêts, interdictions, conversions, urgences,
demandes boutique et SKU à surveiller (pydantic v2).

Un événement est saisi UNE fois et s'applique à toutes les commandes tant qu'il est
actif. Il flague ou bloque ; le planner valide. Aucune quantité n'est inventée : une
quantité imposée ou ajoutée vient toujours d'une saisie humaine, arrondie au multiple
par le moteur (app/engines/events.py).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum

from pydantic import Field, model_validator

from app.domain.models import Sku, _Frozen

ALL = "*"  # « toutes les boutiques » / « tous les SKU » dans la grille des quantités


class EventScope(StrEnum):
    TEAM = "team"  # appliqué pour tous les planners
    PERSONAL = "personal"  # appliqué seulement quand SON planner calcule (ou après « Reprendre »)


class EventType(StrEnum):
    PROMOTION = "promotion"
    LAUNCH = "launch"
    STOP = "stop"  # arrêt d'envoi (do not replenish)
    BAN = "ban"  # interdiction d'envoi (bloqué)
    CONVERSION = "conversion"
    URGENT = "urgent"  # envoi de dernière minute
    STORE_REQUEST = "store_request"  # demande d'une boutique à son planner
    WATCH = "watch"  # à surveiller


class EventEffect(StrEnum):
    FLAG = "flag"  # REVIEW, quantité du moteur inchangée
    SET_QTY = "set_qty"  # quantité imposée
    ADD_QTY = "add_qty"  # quantité ajoutée au besoin calculé
    BLOCK = "block"  # quantité 0, BLOCKED
    REPLACE = "replace"  # conversion : ancien SKU bloqué, nouveau avec historique agrégé


class EventStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"


SCOPE_LABELS = {EventScope.TEAM: "Équipe", EventScope.PERSONAL: "Personnel"}
TYPE_LABELS = {
    EventType.PROMOTION: "Promotion",
    EventType.LAUNCH: "Lancement",
    EventType.STOP: "Arrêt d'envoi",
    EventType.BAN: "Interdiction d'envoi",
    EventType.CONVERSION: "Conversion de SKU",
    EventType.URGENT: "Urgence",
    EventType.STORE_REQUEST: "Demande boutique",
    EventType.WATCH: "À surveiller",
}
EFFECT_LABELS = {
    EventEffect.FLAG: "Flag seulement",
    EventEffect.SET_QTY: "Quantité imposée",
    EventEffect.ADD_QTY: "Quantité ajoutée",
    EventEffect.BLOCK: "Bloquer l'envoi",
    EventEffect.REPLACE: "Remplacer par un autre SKU",
}
STATUS_LABELS = {
    EventStatus.DRAFT: "Brouillon",
    EventStatus.ACTIVE: "Actif",
    EventStatus.DONE: "Terminé",
    EventStatus.CANCELLED: "Annulé",
}

# Effets permis par type (le premier est le défaut proposé).
ALLOWED_EFFECTS: dict[EventType, tuple[EventEffect, ...]] = {
    EventType.PROMOTION: (EventEffect.FLAG, EventEffect.SET_QTY, EventEffect.ADD_QTY),
    EventType.LAUNCH: (EventEffect.SET_QTY, EventEffect.FLAG, EventEffect.ADD_QTY),
    EventType.STOP: (EventEffect.BLOCK, EventEffect.FLAG),
    EventType.BAN: (EventEffect.BLOCK,),
    EventType.CONVERSION: (EventEffect.REPLACE,),
    EventType.URGENT: (EventEffect.SET_QTY, EventEffect.ADD_QTY, EventEffect.FLAG),
    EventType.STORE_REQUEST: (EventEffect.SET_QTY, EventEffect.ADD_QTY),
    EventType.WATCH: (EventEffect.FLAG,),
}
PERSONAL_ONLY = frozenset({EventType.STORE_REQUEST, EventType.WATCH})
OPEN_END_TYPES = frozenset({EventType.STOP, EventType.BAN})  # fin vide = jusqu'à nouvel ordre
# Types à confirmer explicitement avant l'export (« à inclure »).
INCLUDE_TYPES = frozenset({EventType.LAUNCH, EventType.URGENT, EventType.STORE_REQUEST})


def default_lead_days(event_type: EventType) -> int:
    """Jours d'anticipation proposés : 7 pour un lancement, 0 sinon."""
    return 7 if event_type is EventType.LAUNCH else 0


class EventSku(_Frozen):
    """Un SKU concerné. `multiple` : facultatif, pour un lancement absent de la Multiple
    list. `new_sku` : le remplaçant (conversion seulement)."""

    sku: Sku
    multiple: int | None = Field(default=None, ge=1)
    new_sku: Sku | None = None


class EventQty(_Frozen):
    """Quantité saisie : par boutique et par SKU, « * » = toutes / tous."""

    boutique: str = ALL
    sku: str = ALL
    qty: int = Field(ge=0)


class SkuEvent(_Frozen):
    id: int | None = None  # attribué par la base
    scope: EventScope
    type: EventType
    title: str = Field(min_length=1)
    description: str = ""
    skus: tuple[EventSku, ...] = Field(min_length=1)
    boutiques: tuple[str, ...] = ()  # vide (et dcs vide) = toutes les boutiques
    dcs: tuple[str, ...] = ()
    start: date
    end: date | None = None
    lead_days: int = Field(default=0, ge=0, le=120)
    effect: EventEffect
    quantities: tuple[EventQty, ...] = ()
    source_file: str = ""
    owner: str = ""
    planner: str = ""  # planner responsable (obligatoire en portée Personnel)
    status: EventStatus = EventStatus.ACTIVE
    # Demande boutique
    requested_by: str = ""  # champ libre, jamais d'email
    requested_on: date | None = None
    until_next_order: bool = False  # échéance « prochaine commande »
    created_by: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> SkuEvent:
        if not self.title.strip():
            raise ValueError("Le titre est obligatoire")
        if self.effect not in ALLOWED_EFFECTS[self.type]:
            allowed = ", ".join(EFFECT_LABELS[e] for e in ALLOWED_EFFECTS[self.type])
            raise ValueError(
                f"Effet « {EFFECT_LABELS[self.effect]} » impossible pour le type "
                f"« {TYPE_LABELS[self.type]} » (possible : {allowed})"
            )
        if self.type in PERSONAL_ONLY and self.scope is not EventScope.PERSONAL:
            raise ValueError(f"« {TYPE_LABELS[self.type]} » est réservé à la portée Personnel")
        if self.scope is EventScope.PERSONAL and not self.planner.strip():
            raise ValueError("Un événement personnel doit avoir son planner")
        if self.end is None and not (
            self.type in OPEN_END_TYPES
            or (self.type is EventType.STORE_REQUEST and self.until_next_order)
        ):
            raise ValueError("La date de fin est obligatoire pour ce type d'événement")
        if self.end is not None and self.end < self.start:
            raise ValueError("La date de fin est avant la date de début")
        seen: set[str] = set()
        for s in self.skus:
            if s.sku in seen:
                raise ValueError(f"SKU {s.sku} en double dans l'événement")
            seen.add(s.sku)
        if self.effect is EventEffect.REPLACE:
            missing = [s.sku for s in self.skus if not s.new_sku]
            if missing:
                raise ValueError(f"Nouveau SKU manquant pour : {', '.join(missing)}")
            same = [s.sku for s in self.skus if s.new_sku == s.sku]
            if same:
                raise ValueError(f"Un SKU ne peut pas se remplacer lui-même : {', '.join(same)}")
        if self.effect in (EventEffect.SET_QTY, EventEffect.ADD_QTY) and not self.quantities:
            raise ValueError("Saisir au moins une quantité (par boutique ou pour toutes)")
        keys = [(q.boutique, q.sku) for q in self.quantities]
        if len(keys) != len(set(keys)):
            raise ValueError("Quantité saisie deux fois pour la même boutique et le même SKU")
        if "@" in self.requested_by or "@" in self.owner or "@" in self.planner:
            raise ValueError("Pas d'email : un prénom, un rôle ou une équipe seulement")
        if self.type is EventType.STORE_REQUEST and len(self.boutiques) != 1:
            raise ValueError("Une demande boutique concerne une seule boutique")
        return self

    # --- dates -------------------------------------------------------------------

    @property
    def active_from(self) -> date:
        """Premier jour où le SKU est flagué (anticipation comprise)."""
        return self.start - timedelta(days=self.lead_days)

    def in_window(self, day: date) -> bool:
        return self.active_from <= day and (self.end is None or day <= self.end)

    # --- ciblage -------------------------------------------------------------------

    @property
    def all_boutiques(self) -> bool:
        return not self.boutiques and not self.dcs

    def targets(self, boutique: str, dc: str | None = None) -> bool:
        return (
            self.all_boutiques or boutique in self.boutiques or (dc is not None and dc in self.dcs)
        )

    def sku_codes(self) -> list[str]:
        return [s.sku for s in self.skus]

    def qty_for(self, boutique: str, sku: str) -> int | None:
        """Quantité la plus précise : (boutique, SKU) > (boutique, *) > (*, SKU) > (*, *)."""
        table = {(q.boutique, q.sku): q.qty for q in self.quantities}
        for key in ((boutique, sku), (boutique, ALL), (ALL, sku), (ALL, ALL)):
            if key in table:
                return table[key]
        return None

    @property
    def label(self) -> str:
        return f"{TYPE_LABELS[self.type]} « {self.title} »"
