"""Mise en forme compacte des lignes de commande pour la version web.

Partagé par le générateur de la démo (`tools/build_demo_app.py`) et par le moteur qui
tourne dans le navigateur (`app/web/bridge.py`) : les deux produisent exactement le
même format. Aucune quantité n'est calculée ici, on ne fait que recopier le résultat
du moteur.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from app.domain.models import COFFEE_TYPE, Product, RecommendationLine
from app.engines.pallets import OTHERS, PER_PALLET, family


def r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


class Strings:
    """Table de textes dédupliqués (raisons, étapes) pour alléger la page."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self._idx: dict[str, int] = {}

    def id(self, text: str | None) -> int:
        if text is None:
            return -1
        if text not in self._idx:
            self._idx[text] = len(self.items)
            self.items.append(text)
        return self._idx[text]


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:,.0f}".replace(",", " ")


def _fmt1(x: float | None) -> str:
    return "—" if x is None else f"{x:,.1f}".replace(",", " ").replace(".", ",")


def business_step(e: Mapping[str, Any]) -> str | None:
    b = e.get("business")
    if not b:
        return None
    if "allocation" in b:
        a = b["allocation"]
        return (
            f"Allocation officielle : {_fmt(b['allocation_due'])} dus "
            f"(total {_fmt(a['total_qty'])}, vague {a['current_wave']}), "
            f"arrondi au multiple : {_fmt(b['rounded_to_multiple'])}"
        )
    if "launch" in b:
        la = b["launch"]
        d = date.fromisoformat(la["launch_date"])
        return f"Lancement le {d:%d/%m/%Y} : {_fmt(la['qty'])} − Expected, arrondi au multiple"
    if "target" in b:
        return "Stock cible appliqué (voir les raisons)"
    return None


def fallback_step(e: Mapping[str, Any]) -> str | None:
    fb = e.get("fallback")
    if not fb:
        return None
    if fb["daily_rate"]:
        return (
            f"Historique de secours ({fb['weeks_used']} semaine(s) avec ventes sur "
            f"{fb['full_weeks_in_export']} dans l'export) : {_fmt1(fb['daily_rate'])} par jour "
            f"→ {_fmt1(e['cover_applied'])} × {_fmt1(fb['daily_rate'])} arrondi au multiple : "
            f"{_fmt(fb['suggested_qty'])}"
        )
    return (f"Historique de secours : aucune vente dans les semaines déposées "
            f"({fb['full_weeks_in_export']} semaine(s) complète(s) dans l'export)")  # fmt: skip


def pallet_factor(
    line: RecommendationLine, products: Mapping[str, Product], dc: str
) -> float | None:
    fam = family(line)
    if fam == OTHERS:
        prod = products.get(line.sku)
        upp = prod.units_per_pallet if prod else None
        return 1 / upp if upp else None
    return 1 / PER_PALLET[fam].get(dc, PER_PALLET[fam]["default"])


def line_meta(
    line: RecommendationLine,
    products: Mapping[str, Product],
    dc: str,
    excel_qty: Mapping[str, int] | None = None,
    excel_formula: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Ce qui ne dépend pas des jours de couverture : ventes, stock, description…"""
    e, inp = line.explanation, line.explanation["inputs"]
    fb = e.get("fallback") or {}
    return {
        "sku": line.sku,
        "desc": line.description or "",
        "type": line.product_type or "",
        "pfam": family(line),
        "pf": pallet_factor(line, products, dc),
        "coffee": line.product_type == COFFEE_TYPE,
        "sales": inp["sales_total"],
        "sales_own": inp["sales_own"],
        "sales_old": inp["sales_from_old_skus"],
        "exp": inp["expected_total"],
        "exp_own": inp["expected_own"],
        "exp_old": inp["expected_from_old_skus"],
        "dc": inp["dc_available"],
        "cur": r(e["current_cover_days"], 1),
        "pfo": inp.get("in_portfolio"),
        "excl": e.get("exclusion"),
        "fbw": [fb.get("weeks_used"), fb.get("full_weeks_in_export")] if fb else None,
        "xl": (excel_qty or {}).get(line.sku),
        "xlf": (excel_formula or {}).get(line.sku),
    }


def line_tuple(line: RecommendationLine, text: Strings) -> list[Any]:
    """Le résultat du moteur pour une ligne, en tableau compact (ordre lu par la page)."""
    e = line.explanation
    rules = e["rules_triggered"]
    conv = bool(e["inputs"]["sales_from_old_skus"]) or "old_sku_blocked" in rules \
        or any("onversion" in x for x in line.reasons)  # fmt: skip
    flags = (1 if conv else 0) | (2 if ("dc_return" in rules or "dc_out_of_stock" in rules) else 0)
    return [
        line.qty, r(e["raw_need"]), line.status.value[0], [text.id(x) for x in line.reasons],
        e["multiple"], text.id(e["multiple_source"]), line.source.value, line.forecast_qty,
        text.id(fallback_step(e)), text.id(business_step(e)), flags,
    ]  # fmt: skip
