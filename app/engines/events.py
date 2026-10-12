"""Application des événements SKU à une commande. Fonctions PURES : aucune I/O, aucun LLM.

Ordre de priorité sur un même SKU :
1. Problème de conversion (contradiction, chaîne) → BLOCKED.
2. Bloquer l'envoi (Interdiction, Arrêt) ou ancien SKU remplacé → BLOCKED, quantité 0.
   Une interdiction gagne toujours : la quantité d'un autre événement est ignorée,
   ce qui est écrit dans les raisons.
3. Quantité imposée ou ajoutée → remplace / complète la prévision, arrondie au multiple
   supérieur, REVIEW. Plusieurs quantités AJOUTÉES s'additionnent (chacune arrondie),
   REVIEW, chaque événement cité (Q-027). Deux quantités imposées différentes, ou une
   imposée et une ajoutée → BLOCKED « deux événements contradictoires » : jamais de
   choix silencieux.
4. Flag → REVIEW, la quantité du moteur reste.

Une allocation officielle n'est jamais écrasée (règle 5) et une ligne déjà bloquée par
le moteur (exclusion, portfolio, ancien SKU) le reste : la contradiction est écrite.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from app.domain.events import (
    INCLUDE_TYPES,
    SCOPE_LABELS,
    EventEffect,
    EventScope,
    EventSku,
    EventStatus,
    EventType,
    SkuEvent,
)
from app.domain.models import Override, QtySource, RecommendationLine, SkuConversion, Status
from app.engines.conversion import ConversionError, ConversionIndex, build_conversion_index
from app.engines.forecast import ceiling_to_multiple
from app.engines.portfolio import numeric_key

RULE_VERSION = "events-1.1.0"
REASON_CONFLICT = "Deux événements contradictoires"
_SOURCE_FR = {"launch": "lancement du référentiel", "target_stock": "stock cible",
              "dc_return": "retour DC (historique de secours)"}  # fmt: skip
_RANK = {Status.OK: 0, Status.REVIEW: 1, Status.BLOCKED: 2}
# Multiples « peu sûrs » : un multiple saisi dans l'événement passe devant.
_WEAK_MULTIPLES = {"missing_default_1", "excel_default_1", "inferred_family", "excel_zero_multiple"}


# --- État dans le temps --------------------------------------------------------------

UPCOMING, LEAD, ACTIVE, ENDED = "upcoming", "lead", "active", "ended"


def time_state(event: SkuEvent, day: date) -> str:
    """« upcoming » (avant l'anticipation), « lead » (en anticipation), « active », « ended »."""
    if event.end is not None and day > event.end:
        return ENDED
    if day >= event.start:
        return ACTIVE
    if day >= event.active_from:
        return LEAD
    return UPCOMING


def days_left(event: SkuEvent, day: date) -> int | None:
    return None if event.end is None else (event.end - day).days


def same_sku(a: str, b: str) -> bool:
    """Comparaison comme Excel : en numérique puis en texte (« 7005.7 » = « 7005.70 »)."""
    if a.strip() == b.strip():
        return True
    ka = numeric_key(a)
    return ka is not None and ka == numeric_key(b)


# --- Quels événements pour cette commande ? ----------------------------------------


def applies_to_planner(event: SkuEvent, planner: str, taken_over: Iterable[int] = ()) -> bool:
    """Équipe : tout le monde. Personnel : son planner, ou un collègue qui l'a repris."""
    if event.scope is EventScope.TEAM:
        return True
    mine = bool(planner.strip()) and event.planner.strip().lower() == planner.strip().lower()
    return mine or (event.id is not None and event.id in set(taken_over))


def select_for_order(
    events: Iterable[SkuEvent],
    *,
    boutique: str,
    dc: str | None,
    delivery: date,
    planner: str,
    taken_over: Iterable[int] = (),
) -> list[SkuEvent]:
    """Événements actifs (début − anticipation ≤ livraison ≤ fin) pour la boutique et le planner."""
    taken = set(taken_over)
    return [
        e
        for e in events
        if e.status is EventStatus.ACTIVE
        and e.in_window(delivery)
        and e.targets(boutique, dc)
        and applies_to_planner(e, planner, taken)
    ]


def forced_skus(events: Iterable[SkuEvent]) -> list[str]:
    """Tous les SKU cités (et les nouveaux SKU des conversions) : jamais oubliés."""
    out: list[str] = []
    for e in events:
        for s in e.skus:
            out.append(s.sku)
            if e.effect is EventEffect.REPLACE and s.new_sku:
                out.append(s.new_sku)
    return list(dict.fromkeys(out))


# --- Conversions apportées par les événements --------------------------------------


@dataclass(frozen=True)
class EventConversions:
    index: ConversionIndex
    problems: dict[str, str] = field(default_factory=dict)  # SKU -> raison du blocage


def merge_conversions(
    index: ConversionIndex, events: Iterable[SkuEvent], run_date: date
) -> EventConversions:
    """Ajoute les conversions des événements actifs au moteur de conversion existant.

    L'ancien SKU est bloqué dès aujourd'hui, le nouveau additionne ventes et Expected.
    Une contradiction avec SKU Conversion ou une chaîne A → B → C bloque les deux SKU.
    """
    base = dict(index.by_old)
    news = {c.new_sku for convs in index.by_new.values() for c in convs}
    added: dict[str, SkuConversion] = {}
    problems: dict[str, str] = {}
    for e in events:
        if e.effect is not EventEffect.REPLACE:
            continue
        for s in e.skus:
            old, new = s.sku, s.new_sku or ""
            known = base.get(old) or added.get(old)
            if known is not None:
                if known.new_sku != new:
                    why = (
                        f"Conversion contradictoire : {e.label} remplace {old} par {new}, "
                        f"mais {old} est déjà converti vers {known.new_sku}"
                    )
                    problems[old] = problems[new] = why
                continue
            if old in news or new in base or new in added:
                why = f"Chaîne de conversion non gérée ({e.label}) : {old} → {new}"
                problems[old] = problems[new] = why
                continue
            added[old] = SkuConversion(
                old_sku=old,
                new_sku=new,
                effective_date=run_date - timedelta(days=1),
                comment=f"Événement {e.id or ''} {e.title}".strip(),
            )
            news.add(new)
    if not added:
        return EventConversions(index, problems)
    try:
        merged = build_conversion_index([*base.values(), *added.values()])
    except ConversionError as exc:  # sécurité : jamais d'index incohérent
        for old, conv in added.items():
            problems[old] = problems[conv.new_sku] = f"Conversion impossible : {exc}"
        return EventConversions(index, problems)
    return EventConversions(merged, problems)


# --- Application sur les lignes -----------------------------------------------------


@dataclass(frozen=True)
class _Hit:
    event: SkuEvent
    item: EventSku
    role: str  # "sku" | "new" (nouveau SKU d'une conversion)


def _tag(hit: _Hit, delivery: date) -> dict[str, Any]:
    e = hit.event
    return {
        "id": e.id,
        "title": e.title,
        "type": e.type.value,
        "scope": e.scope.value,
        "effect": e.effect.value,
        "planner": e.planner,
        "role": hit.role,
        "start": e.start.isoformat(),
        "end": e.end.isoformat() if e.end else None,
        "lead": delivery < e.start,
    }


def _who(e: SkuEvent) -> str:
    scope = SCOPE_LABELS[e.scope]
    return f"{scope}, {e.planner}" if e.scope is EventScope.PERSONAL and e.planner else scope


def _hits(sku: str, events: Sequence[SkuEvent]) -> list[_Hit]:
    out = []
    for e in events:
        for s in e.skus:
            if same_sku(s.sku, sku):
                out.append(_Hit(e, s, "sku"))
            elif e.effect is EventEffect.REPLACE and s.new_sku and same_sku(s.new_sku, sku):
                out.append(_Hit(e, s, "new"))
    return out


def _multiple(line: RecommendationLine, hits: Sequence[_Hit]) -> tuple[int, list[str], bool]:
    """Multiple à utiliser pour une quantité d'événement (+ raisons, + changé ?)."""
    m = int(line.explanation.get("multiple") or 0)
    source = line.explanation.get("multiple_source", "")
    given = sorted({h.item.multiple for h in hits if h.item.multiple})
    notes: list[str] = []
    if not given:
        return (m if m > 0 else 1), notes, False
    if len(given) > 1:
        notes.append(f"Multiples différents dans les événements ({' / '.join(map(str, given))})")
    if source in _WEAK_MULTIPLES or m <= 0:
        notes.append(f"Multiple {given[0]} saisi dans l'événement (absent de la Multiple list)")
        return given[0], notes, True
    if given[0] != m:
        notes.append(
            f"Multiple {given[0]} de l'événement différent de la Multiple list ({m}) : {m} retenu"
        )
    return m, notes, False


def _one_per_event(given: list[tuple[_Hit, int]]) -> list[tuple[_Hit, int]]:
    """Une quantité par événement : un SKU listé deux fois dans un événement compte une fois."""
    seen: set[int] = set()
    out = []
    for h, q in given:
        key = h.event.id if h.event.id is not None else id(h.event)
        if key not in seen:
            seen.add(key)
            out.append((h, q))
    return out


def _qty_text(effect: EventEffect, qty: int) -> str:
    return f"imposée {qty}" if effect is EventEffect.SET_QTY else f"ajoutée {qty}"


def apply_events(
    lines: Sequence[RecommendationLine],
    events: Sequence[SkuEvent],
    *,
    boutique: str,
    delivery: date,
    problems: Mapping[str, str] | None = None,
) -> list[RecommendationLine]:
    """Applique les événements déjà sélectionnés (voir `select_for_order`)."""
    if not events:
        return list(lines)
    problems = problems or {}
    out: list[RecommendationLine] = []
    for line in lines:
        hits = _hits(line.sku, events)
        problem = next((why for sku, why in problems.items() if same_sku(sku, line.sku)), None)
        if not hits and problem is None:
            out.append(line)
            continue
        out.append(_apply_line(line, hits, boutique, delivery, problem))
    return out


def _apply_line(
    line: RecommendationLine,
    hits: Sequence[_Hit],
    boutique: str,
    delivery: date,
    problem: str | None,
) -> RecommendationLine:
    reasons: list[str] = []
    status, qty, source = line.status, line.qty, line.source
    explanation = dict(line.explanation)
    decision = "flag"

    def raise_to(s: Status) -> None:
        nonlocal status
        if _RANK[s] > _RANK[status]:
            status = s

    def lead(e: SkuEvent) -> str:
        return f" (anticipation : début le {e.start:%d/%m})" if delivery < e.start else ""

    blocks = [h for h in hits if h.event.effect is EventEffect.BLOCK
              or (h.event.effect is EventEffect.REPLACE and h.role == "sku")]  # fmt: skip
    quantities = [h for h in hits if h.event.effect in (EventEffect.SET_QTY, EventEffect.ADD_QTY)]
    flags = [h for h in hits if h.event.effect is EventEffect.FLAG]
    new_side = [h for h in hits if h.event.effect is EventEffect.REPLACE and h.role == "new"]

    for h in flags:
        raise_to(Status.REVIEW)
        reasons.append(f"{h.event.label} ({_who(h.event)}) : à vérifier{lead(h.event)}")
    for h in new_side:
        raise_to(Status.REVIEW)
        reasons.append(f"{h.event.label} : remplace {h.item.sku}, historique agrégé{lead(h.event)}")

    if problem is not None:
        qty, source, decision = 0, QtySource.EVENT, "blocked_problem"
        raise_to(Status.BLOCKED)
        reasons.append(problem)
    elif blocks:
        qty, source, decision = 0, QtySource.EVENT, "blocked"
        raise_to(Status.BLOCKED)
        for h in blocks:
            if h.event.effect is EventEffect.REPLACE:
                reasons.append(f"{h.event.label} : remplacé par {h.item.new_sku}, ne plus envoyer")
            else:
                reasons.append(f"{h.event.label} ({_who(h.event)}) : envoi bloqué{lead(h.event)}")
        if quantities:
            others = ", ".join(h.event.label for h in quantities)
            reasons.append(f"Le blocage prime sur {others} : quantité 0")
    elif quantities:
        defined = [(h, h.event.qty_for(boutique, line.sku)) for h in quantities]
        missing = [h for h, q in defined if q is None]
        given = [(h, q) for h, q in defined if q is not None]
        raise_to(Status.REVIEW)
        for h in missing:
            reasons.append(
                f"{h.event.label} ({_who(h.event)}) : quantité non définie pour {boutique}, "
                f"quantité du moteur gardée ({line.qty}){lead(h.event)}"
            )
        given = _one_per_event(given)
        sets = [(h, q) for h, q in given if h.event.effect is EventEffect.SET_QTY]
        adds = [(h, q) for h, q in given if h.event.effect is EventEffect.ADD_QTY]
        if not given:
            decision = "missing_qty"
        elif line.source is QtySource.ALLOCATION:
            decision = "allocation_kept"
            for h, q in given:
                reasons.append(
                    f"Allocation officielle conservée ({line.qty}) : {h.event.label} "
                    f"proposait {_qty_text(h.event.effect, q)}"
                )
        elif line.status is Status.BLOCKED:
            decision = "line_blocked"
            for h, q in given:
                reasons.append(
                    f"{h.event.label} : quantité {_qty_text(h.event.effect, q)} demandée mais la "
                    "ligne est bloquée par le moteur : à trancher par une modification"
                )
        elif (sets and adds) or len({q for _, q in sets}) > 1:
            # Imposée contre ajoutée, ou deux quantités imposées différentes (Q-027).
            qty, source, decision = 0, QtySource.EVENT, "conflict"
            raise_to(Status.BLOCKED)
            listed = " ; ".join(f"{h.event.label} ({_qty_text(h.event.effect, q)})"
                                for h, q in given)  # fmt: skip
            reasons.append(f"{REASON_CONFLICT} : {listed} → quantité 0, à trancher")
        else:
            multiple, notes, changed = _multiple(line, hits)
            reasons.extend(notes)
            if changed:
                explanation["multiple"] = multiple
                explanation["multiple_source"] = "event"
            if sets:
                h, q = sets[0]
                rounded = ceiling_to_multiple(Fraction(q), multiple)
                qty, decision = rounded, "set_qty"
                text = f"quantité imposée {q}"
                if rounded != q:
                    text += f" (arrondi au multiple de {multiple} : {rounded})"
                reasons.append(f"{h.event.label} ({_who(h.event)}) : {text} → {qty}"
                               f"{lead(h.event)} ; prévision {line.qty}")  # fmt: skip
                if len(sets) > 1:
                    reasons.append(
                        "Plusieurs événements imposent la même quantité : appliquée une fois"
                    )
            else:
                # Q-027 : plusieurs quantités ajoutées s'additionnent (chacune arrondie au
                # multiple supérieur, comme si elle était seule), REVIEW, chaque événement cité.
                rounded_adds = [ceiling_to_multiple(Fraction(q), multiple) for _, q in adds]
                qty = line.qty + sum(rounded_adds)
                decision = "add_qty" if len(adds) == 1 else "add_qty_sum"
                for (h, q), rounded in zip(adds, rounded_adds, strict=True):
                    text = f"{line.qty} calculé + {q} ajouté" if len(adds) == 1 else f"{q} ajouté"
                    if rounded != q:
                        text += f" (arrondi au multiple de {multiple} : {rounded})"
                    end = f" → {qty}" if len(adds) == 1 else ""
                    reasons.append(
                        f"{h.event.label} ({_who(h.event)}) : {text}{end}{lead(h.event)}"
                    )
                if len(adds) > 1:
                    parts = " + ".join(map(str, rounded_adds))
                    reasons.append(f"Quantités ajoutées additionnées : {line.qty} calculé + "
                                   f"{parts} = {qty}")  # fmt: skip
            source = QtySource.EVENT
            if line.source is not QtySource.FORECAST:
                was = _SOURCE_FR.get(line.source.value, line.source.value)
                reasons.append(f"La quantité de l'événement remplace : {was} ({line.qty})")

    explanation["events"] = {
        "rule_version": RULE_VERSION,
        "applied": [_tag(h, delivery) for h in hits],
        "decision": decision,
        "qty_before": line.qty,
        "problem": problem,
    }
    return line.model_copy(
        update={
            "qty": qty,
            "status": status,
            "source": source,
            "forecast_qty": line.qty if line.forecast_qty is None else line.forecast_qty,
            "reasons": [*line.reasons, *reasons],
            "explanation": explanation,
        }
    )


# --- Garde-fou avant export ---------------------------------------------------------


def event_tags(line: RecommendationLine) -> list[dict[str, Any]]:
    ev = line.explanation.get("events")
    return list(ev["applied"]) if ev else []


def blocked_by_event(line: RecommendationLine) -> bool:
    return line.status is Status.BLOCKED and bool(line.explanation.get("events"))


@dataclass(frozen=True)
class ExportCheck:
    """Les trois points à mettre au vert avant de télécharger le fichier LT."""

    blocked: tuple[str, ...]  # BLOCKED par un événement, pas encore traités
    unvalidated: tuple[str, ...]  # flagués par un événement, pas encore « Vu »
    to_include: tuple[tuple[str, str], ...]  # (SKU, titre) lancements / urgences / demandes
    include_confirmed: bool

    @property
    def include_ok(self) -> bool:
        return not self.to_include or self.include_confirmed

    @property
    def ok(self) -> bool:
        return not self.blocked and not self.unvalidated and self.include_ok


def export_check(
    base_lines: Iterable[RecommendationLine],
    *,
    overrides: Mapping[str, Override],
    seen: Iterable[str],
    confirmed_blocks: Iterable[str],
    include_confirmed: bool,
) -> ExportCheck:
    """`base_lines` = lignes AVANT modifications du planner.

    Une ligne BLOCKED par un événement est traitée si le planner confirme le blocage, ou
    s'il la modifie avec une raison (override justifié). Une ligne flaguée est validée
    si elle est marquée « Vu » ou modifiée.
    """
    seen, confirmed = set(seen), set(confirmed_blocks)
    blocked, unvalidated, include = [], [], []
    for line in base_lines:
        tags = event_tags(line)
        if not tags:
            continue
        ov = overrides.get(line.sku)
        if blocked_by_event(line):
            if line.sku not in confirmed and not (ov is not None and ov.reason.strip()):
                blocked.append(line.sku)
            continue
        if line.sku not in seen and ov is None:
            unvalidated.append(line.sku)
        titles = [t["title"] for t in tags if EventType(t["type"]) in INCLUDE_TYPES]
        if titles:
            include.append((line.sku, " / ".join(dict.fromkeys(titles))))
    return ExportCheck(tuple(blocked), tuple(unvalidated), tuple(include), include_confirmed)


def store_requests_done(events: Iterable[SkuEvent]) -> list[SkuEvent]:
    """Demandes boutique appliquées à la commande : à terminer après l'export (confirmé)."""
    return [e for e in events if e.type is EventType.STORE_REQUEST]
