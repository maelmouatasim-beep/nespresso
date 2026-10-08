"""État de session, navigation et formatage partagés par les écrans."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from app.domain.models import (
    OVERRIDE_CATEGORY_LABELS,
    ForecastMode,
    Override,
    RecommendationLine,
    Status,
)
from app.engines.categories import category_of
from app.services.planning import PlanningResult, with_overrides
from app.ui.workspace import DEMO_EXTRACTION, DayData, Workspace, demo_workspace, real_workspace

PAGES = (
    "1. Dépôt du jour",
    "2. Commande boutique",
    "3. Journée",
    "4. Export",
    "5. Référentiels",
    "6. Historique",
)
DEPOT, ORDER, DAY, EXPORT, REFS, HISTORY = PAGES

MODES = {"Standard": ForecastMode.STANDARD, "Parité Excel": ForecastMode.EXCEL_PARITY}
MODE_LABEL = {v.value: k for k, v in MODES.items()} | {"safe": "Standard"}
# Statuts : pastille + texte. Couleurs de la charte (docs/charte_visuelle.md), contraste ≥ 4,5:1.
STATUS_ICON = {"OK": "● OK", "REVIEW": "● REVIEW", "BLOCKED": "● BLOCKED"}  # grille
MODIFIED = "● Modifié"
STATUS_MD = {"OK": ":green[● OK]", "REVIEW": ":orange[● REVIEW]", "BLOCKED": ":red[● BLOCKED]"}
MODIFIED_MD = ":blue[● Modifié]"
STATUS_COLORS = {  # texte, fond
    "OK": ("#2A7449", "#E6F2EA"),
    "REVIEW": ("#94600F", "#FFF4DF"),
    "BLOCKED": ("#B23A3A", "#FBE9E7"),
    "Modifié": ("#2F5F9E", "#E8F0FA"),
}
ZEBRA = "#FBF8F3"  # lignes alternées très légères
SOURCE_LABEL = {
    "forecast": "Prévision",
    "allocation": "Allocation",
    "launch": "Lancement",
    "target_stock": "Stock cible",
    "dc_return": "Retour DC",
    "exclusion": "Exclusion",
    "override": "Modifié",
}
CATEGORY_BY_LABEL = {label: cat for cat, label in OVERRIDE_CATEGORY_LABELS.items()}
FRESH_ICON = {
    "green": ":green[:material/check_circle:]",
    "orange": ":orange[:material/schedule:]",
    "red": ":red[:material/error:]",
    "unknown": ":gray[:material/help:]",
}

ss = st.session_state


def init_state() -> None:
    defaults = {
        "page": DEPOT,
        "ws_kind": "Mes données",
        "day": None,
        "day_error": None,
        "base": None,  # PlanningResult avant modifications
        "overrides": {},  # sku -> Override
        "seen": set(),  # SKU vérifiés par le planner
        "saved_run_id": None,
        "day_outcomes": None,
        "nonce": 0,
        "why_sku": None,
        "author": "",
    }
    for k, v in defaults.items():
        ss.setdefault(k, v)


def go(page: str) -> None:
    ss.page = page


def workspace() -> Workspace:
    return demo_workspace() if ss.ws_kind == "Démo B80" else real_workspace()


def is_demo() -> bool:
    return ss.ws_kind == "Démo B80"


def run_date() -> date:
    """Jour du calcul : aujourd'hui (démo : jour des données de démonstration)."""
    return DEMO_EXTRACTION if is_demo() else date.today()


def reset_order() -> None:
    ss.base, ss.overrides, ss.seen = None, {}, set()
    ss.saved_run_id, ss.why_sku = None, None
    ss.nonce += 1


def reset_all() -> None:
    reset_order()
    ss.day, ss.day_error, ss.day_outcomes = None, None, None


@st.cache_resource(max_entries=4, show_spinner="Lecture des exports du jour…")
def _load_day(root: str, signature: str) -> DayData:
    """Partagé entre les onglets et les sessions tant que les fichiers ne changent pas."""
    del signature  # sert seulement de clé de cache
    return Workspace(Path(root)).load_day()


def _signature(ws: Workspace) -> str:
    """Empreinte des fichiers utilisés : un dépôt ou un référentiel modifié la change."""
    files = [p for _, (p, _) in sorted(ws.latest_depot().items())]
    if ws.masters_dir.exists():
        files += sorted(ws.masters_dir.iterdir())
    return "|".join(f"{p.name}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in files)


def day_data() -> DayData | None:
    """Données du jour (chargées une fois, ou après un nouveau dépôt / référentiel)."""
    if ss.day is None and ss.day_error is None:
        ws = workspace()
        try:
            ss.day = _load_day(str(ws.root), _signature(ws))
        except (ValueError, OSError) as exc:
            ss.day_error = str(exc)
    return ss.day


def current() -> PlanningResult | None:
    if ss.base is None or ss.day is None:
        return None
    return with_overrides(ss.base, ss.overrides.values(), ss.day.masters.products)


def author() -> str:
    return (ss.get("author") or "").strip()


def boutique_label(code: str, names: dict[str, str]) -> str:
    return f"{code} — {names[code]}" if code in names else code


def fmt_int(x: float | None) -> str:
    """Entier avec séparateur de milliers (espace fine) : « 37 061 »."""
    return "—" if x is None else f"{x:,.0f}".replace(",", "\u202f")


def fmt_1(x: float | None) -> str:
    """Une décimale, virgule française : « 7,3 »."""
    return "—" if x is None else f"{x:,.1f}".replace(",", "\u202f").replace(".", ",")


FR_DAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


def fr_day(d: date) -> str:
    """« jeu. 08/10 »."""
    return f"{FR_DAYS[d.weekday()]} {d:%d/%m}"


def fmt_date(d: date | datetime | None) -> str:
    return "—" if d is None else d.strftime("%d/%m/%Y")


def num(x: float | None) -> float:
    return float("nan") if x is None else float(x)


def status_cell(line: RecommendationLine) -> str:
    """Statut affiché : « Modifié » prime ; « ✓ » quand le planner a validé la ligne."""
    label = MODIFIED if line.sku in ss.overrides else STATUS_ICON[line.status.value]
    return f"{label} ✓" if line.sku in ss.seen else label


def make_override(
    sku: str, before: int, after: int, category_label: str | None = None, reason: str = ""
) -> Override:
    """Modification du planner : auteur et heure toujours gardés, catégorie et raison facultatives."""
    return Override(
        sku=sku,
        qty_before=before,
        qty_after=after,
        category=CATEGORY_BY_LABEL[category_label] if category_label else None,
        reason=reason.strip(),
        author=author() or "Planner non renseigné",
        timestamp=datetime.now(),
    )


def category_of_line(line: RecommendationLine, mapping: dict[str, str]) -> str:
    return category_of(line.product_type, mapping)


def _round1(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


def order_frame(
    lines: list[RecommendationLine], base_qty: dict[str, int], mapping: dict[str, str]
) -> pd.DataFrame:
    """Une seule grille, colonnes dans l'ordre du planner ; nombres en vrais nombres (tri)."""
    rows = []
    for x in lines:
        e, inp = x.explanation, x.explanation["inputs"]
        sales = inp["sales_total"]
        days = e["parameters"]["sales_history_days"]
        after = (inp["expected_total"] + x.qty) / sales * days if sales else None
        rows.append(
            {
                "Détail": x.sku == ss.get("why_sku"),
                "Statut": status_cell(x),
                "SKU": x.sku,
                "Description": x.description or "",
                "Catégorie": category_of_line(x, mapping),
                "Expected": num(inp["expected_total"]),
                "Ventes": num(sales),
                "Couv. actuelle": num(_round1(e["current_cover_days"])),
                "Qty proposée": base_qty.get(x.sku, x.qty),
                "Qty finale": x.qty,
                "Couv. après": num(_round1(after)),
                "Multiple": num(e["multiple"]),
                "Stock DC": num(inp["dc_available"]),
                "Source": SOURCE_LABEL[x.source.value],
                "Raisons": " | ".join(x.reasons),
            }
        )
    return pd.DataFrame(rows, columns=ORDER_COLUMNS)


ORDER_COLUMNS = [
    "Détail", "Statut", "SKU", "Description", "Catégorie", "Expected", "Ventes", "Couv. actuelle",
    "Qty proposée", "Qty finale", "Couv. après", "Multiple", "Stock DC", "Source", "Raisons",
]  # fmt: skip

_INT = {"format": "localized"}
ORDER_COLUMN_CONFIG = {
    "Détail": st.column_config.CheckboxColumn(
        "Détail", width=72, help="Pourquoi cette quantité ?", pinned=True
    ),
    "Statut": st.column_config.TextColumn(width=110),
    "SKU": st.column_config.TextColumn(width=100),
    "Description": st.column_config.TextColumn(width=210),
    "Catégorie": st.column_config.TextColumn(width=120),
    "Expected": st.column_config.NumberColumn(width=80, **_INT),
    "Ventes": st.column_config.NumberColumn(width=70, **_INT),
    "Couv. actuelle": st.column_config.NumberColumn("Couv. act.", width=75, format="%.1f"),
    "Qty proposée": st.column_config.NumberColumn("Proposée", width=80, **_INT),
    "Qty finale": st.column_config.NumberColumn(
        "Qty finale", width=90, min_value=0, step=1, help="Modifiable", **_INT
    ),
    "Couv. après": st.column_config.NumberColumn("Couv. après", width=80, format="%.1f"),
    "Multiple": st.column_config.NumberColumn(width=70, **_INT),
    "Stock DC": st.column_config.NumberColumn(width=85, **_INT),
    "Source": st.column_config.TextColumn(width=95),
    "Raisons": st.column_config.TextColumn(width=340),
}
EDITABLE_COLUMNS = ("Détail", "Qty finale")


def _status_style(value: str) -> str:
    for key, (fg, bg) in STATUS_COLORS.items():
        if key in value:
            return f"color: {fg}; background-color: {bg}; font-weight: 600"
    return ""


def style_order_frame(df: pd.DataFrame):  # noqa: ANN201 - pandas Styler
    """Lignes alternées très légères + pastilles de statut colorées."""
    zebra = df.style.apply(
        lambda row: [f"background-color: {ZEBRA}" if row.name % 2 else ""] * len(row), axis=1
    )
    return zebra.map(_status_style, subset=["Statut"])


def default_order(
    lines: list[RecommendationLine], base_qty: dict[str, int]
) -> list[RecommendationLine]:
    """Tri par défaut : REVIEW et BLOCKED en haut, puis Qty proposée décroissante."""
    return sorted(lines, key=lambda x: (x.status is Status.OK, -base_qty.get(x.sku, x.qty), x.sku))


def explain_steps(line: RecommendationLine) -> list[str]:
    """Le calcul pas à pas, en français simple."""
    e, inp = line.explanation, line.explanation["inputs"]
    p = e["parameters"]
    days = p["sales_history_days"]
    period = p.get("sales_period") or [None, None]
    steps = []
    if period[0]:
        start, end = (date.fromisoformat(d) for d in period)
        when = f" (du {start:%d/%m} au {end:%d/%m})"
    else:
        when = ""
    sales = inp["sales_total"]
    steps.append(f"Ventes des {days} derniers jours{when} : **{fmt_int(sales)}**")
    for old, s in inp["sales_from_old_skus"].items():
        steps.append(
            f"dont l'ancien SKU {old} (conversion) : ventes {fmt_int(s)}, "
            f"Expected {fmt_int(inp['expected_from_old_skus'][old])}"
        )
    steps.append(f"Stock attendu en boutique (Expected) : **{fmt_int(inp['expected_total'])}**")
    cover = e["cover_applied"]
    cover_txt = f"{fmt_1(cover)} jours" + (" (cover café)" if e["cover_source"] ==
                                           "coffee_cover_days" else "")  # fmt: skip
    if e["exclusion"]:
        ex = e["exclusion"]
        steps.append(f"**Exclusion** : {ex['reason']} (ajoutée par {ex['author']} le "
                     f"{fmt_date(date.fromisoformat(ex['updated_on']))}) → quantité 0")  # fmt: skip
    elif e["raw_need"] is not None:
        need = cover * sales / days if days else 0
        steps.append(f"Besoin pour {cover_txt} : {fmt_1(cover)} × {fmt_int(sales)} ÷ {days} = "
                     f"**{fmt_1(need)}**")  # fmt: skip
        steps.append(f"Moins l'Expected : {fmt_1(need)} − {fmt_int(inp['expected_total'])} = "
                     f"**{fmt_1(e['raw_need'])}**")  # fmt: skip
        if e["raw_need"] > 0:
            steps.append(
                f"Arrondi au multiple de {e['multiple']} supérieur : **{fmt_int(e['qty'])}**"
            )
        else:
            steps.append("Le stock couvre déjà le besoin : **0**")
    else:
        steps.append("Pas de calcul : la ligne est bloquée (voir les raisons)")
    fb = e.get("fallback")
    if fb:
        if fb["daily_rate"]:
            steps.append(
                f"**Historique de secours** ({fb['weeks_used']} semaine(s) avec ventes sur "
                f"{fb['full_weeks_in_export']} dans l'export) : {fmt_1(fb['daily_rate'])} par jour → "
                f"{fmt_1(cover)} × {fmt_1(fb['daily_rate'])} arrondi au multiple : "
                f"**{fmt_int(fb['suggested_qty'])}**"
            )
        else:
            steps.append("**Historique de secours** : aucune vente dans les semaines déposées")
    business = e.get("business")
    if business and "allocation" in business:
        a = business["allocation"]
        steps.append(f"**Allocation officielle** : {fmt_int(business['allocation_due'])} dus "
                     f"(total {fmt_int(a['total_qty'])}, vague {a['current_wave']}), arrondi au "
                     f"multiple : **{fmt_int(business['rounded_to_multiple'])}**")  # fmt: skip
    elif business and "launch" in business:
        la = business["launch"]
        steps.append(f"**Lancement** le {fmt_date(date.fromisoformat(la['launch_date']))} : "
                     f"{fmt_int(la['qty'])} − Expected, arrondi au multiple")  # fmt: skip
    elif business and "target" in business:
        steps.append("**Stock cible** appliqué (voir les raisons)")
    if e.get("override"):
        o = e["override"]
        steps.append(f"**Modifié** par {o['author']} : {fmt_int(o['qty_before'])} → "
                     f"{fmt_int(o['qty_final'])} ({o['reason']})")  # fmt: skip
    steps.append(f"**Quantité finale : {fmt_int(line.qty)}**")
    return steps
