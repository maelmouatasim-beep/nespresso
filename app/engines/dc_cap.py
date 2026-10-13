"""Plafonnement au stock DC (mode Safe). Fonction pure, sans I/O.

Règle du planner (D-051, 12-oct-2026, remplace la double confirmation D-048) :

- En mode Safe (le mode par défaut), la Qty proposée est plafonnée au stock DC disponible,
  arrondie au multiple INFÉRIEUR : DC = 0 → 0 ; DC = 500 avec un multiple de 240 → 480.
- La ligne garde son statut (OK reste OK) : elle se confirme d'un clic. La raison dit
  « Plafonné au stock DC (besoin 1 200, DC 500 → 480) » et la cellule Stock DC est orange.
- Les quantités imposées par un événement (lancement, allocation) suivent la même règle.
- Le mode « Parité Excel » ne plafonne jamais (Excel ne le fait pas) : il garde le signal
  REVIEW « Stock DC insuffisant » du moteur de prévision ; la parité 416/416 est intacte.
- Un SKU absent de l'export DC n'est pas plafonné (stock inconnu) : il reste en REVIEW
  « Stock DC insuffisant : SKU absent du stock DC » (rien de silencieux, Q-033).
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

from app.domain.models import ForecastMode, RecommendationLine, Status

RULE_VERSION = "dc_cap-1.0.0"
REASON_CAPPED = "Plafonné au stock DC"
REASON_OVER = "Dépasse le stock DC"


def _n(x: float | int) -> str:
    """Nombre entier avec espace comme séparateur de milliers (1 200)."""
    return f"{float(x):,.0f}".replace(",", " ")


def floor_to_multiple(value: float | Fraction, multiple: int) -> int:
    """Arrondi au multiple INFÉRIEUR (jamais au-dessus du stock DC) ; multiple ≤ 0 → 1."""
    m = multiple if multiple > 0 else 1
    v = Fraction(value)
    if v <= 0:
        return 0
    return int(v // m) * m


def dc_cap_of(line: RecommendationLine) -> dict | None:
    """Détail du plafonnement appliqué à la ligne, ou None."""
    cap = line.explanation.get("dc_cap")
    return dict(cap) if cap else None


def cap_line(line: RecommendationLine, mode: ForecastMode) -> RecommendationLine:
    """Plafonne une ligne au stock DC (mode Safe seulement). Une ligne bloquée, à 0, ou
    dont le DC est inconnu n'est pas touchée."""
    if mode is not ForecastMode.STANDARD or line.qty <= 0 or line.status is Status.BLOCKED:
        return line
    dc = line.explanation["inputs"].get("dc_available")
    if dc is None or dc >= line.qty:
        return line
    multiple = int(line.explanation.get("multiple") or 1)
    capped = floor_to_multiple(dc, multiple)
    explanation = dict(line.explanation)
    explanation["dc_cap"] = {"need": line.qty, "dc": dc, "qty": capped, "multiple": multiple,
                             "rule_version": RULE_VERSION}  # fmt: skip
    explanation["rules_triggered"] = [*line.explanation.get("rules_triggered", []), "dc_cap"]
    reason = f"{REASON_CAPPED} (besoin {_n(line.qty)}, DC {_n(dc)} → {_n(capped)})"
    return line.model_copy(
        update={
            "qty": capped,
            "forecast_qty": line.qty if line.forecast_qty is None else line.forecast_qty,
            "reasons": [*line.reasons, reason],
            "explanation": explanation,
        }
    )


def cap_to_dc(lines: Sequence[RecommendationLine], mode: ForecastMode) -> list[RecommendationLine]:
    """Applique `cap_line` à chaque ligne (après l'arbitrage métier et les événements)."""
    return [cap_line(x, mode) for x in lines]


def over_dc(line: RecommendationLine, qty: int) -> str | None:
    """Mode Safe : une quantité saisie au-dessus du stock DC → raison « Dépasse le stock DC »
    (la ligne passe en REVIEW, à confirmer). Sinon None."""
    if line.explanation.get("mode") != ForecastMode.STANDARD.value or qty <= 0:
        return None
    dc = line.explanation["inputs"].get("dc_available")
    if dc is None:
        return f"{REASON_OVER} : SKU absent du stock DC"
    if dc < qty:
        return f"{REASON_OVER} (DC {_n(dc)} < {_n(qty)})"
    return None
