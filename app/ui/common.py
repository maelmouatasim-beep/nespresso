"""État de session, navigation et formatage partagés par les écrans."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from app.domain.events import SkuEvent
from app.domain.models import (
    ForecastMode,
    Override,
    RecommendationLine,
    Status,
)
from app.engines.categories import category_of
from app.engines.events import event_tags
from app.engines.overrides import deduce_context
from app.services.planning import EventContext, PlanningResult, with_overrides
from app.storage import db
from app.storage import events as event_store
from app.ui.workspace import DEMO_EXTRACTION, DayData, Workspace, demo_workspace, real_workspace

PAGES = (
    "1. Dépôt du jour",
    "2. Commandes",
    "3. Export",
    "4. Événements SKU",
    "5. Référentiels",
    "6. Historique",
)
DEPOT, ORDER, EXPORT, EVENTS, REFS, HISTORY = PAGES

MODES = {"Standard": ForecastMode.STANDARD, "Parité Excel": ForecastMode.EXCEL_PARITY}
MODE_LABEL = {v.value: k for k, v in MODES.items()} | {"safe": "Standard"}
# Statuts : pastille + texte. Couleurs de la charte (docs/charte_visuelle.md), contraste ≥ 4,5:1.
# D-039 : le statut vient du calcul et ne change jamais quand la qty est modifiée ; seul un clic
# « Confirmé » le fait passer à « Confirmé ». « modifié » est un marqueur, pas un statut.
STATUS_ICON = {"OK": "● OK", "REVIEW": "● REVIEW", "BLOCKED": "● BLOCKED"}  # grille
CONFIRMED = "● Confirmé"
MODIFIED_MARK = " · modifié"
STATUS_MD = {"OK": ":green[● OK]", "REVIEW": ":orange[● REVIEW]", "BLOCKED": ":red[● BLOCKED]"}
CONFIRMED_MD = ":green[● Confirmé]"
MODIFIED_MD = ":blue[· modifié]"
MODIFIED_BG = "#E8F0FA"  # fond bleu très léger de la cellule Qty finale modifiée
STATUS_COLORS = {  # texte, fond
    "Confirmé": ("#2A7449", "#E6F2EA"),
    "OK": ("#2A7449", "#E6F2EA"),
    "REVIEW": ("#94600F", "#FFF4DF"),
    "BLOCKED": ("#B23A3A", "#FBE9E7"),
}
ZEBRA = "#FBF8F3"  # lignes alternées très légères
COVER_LOW, COVER_OK = "#B23A3A", "#2A7449"  # couverture sous / au-dessus de la cible (D-046)
SOURCE_LABEL = {
    "forecast": "Prévision",
    "allocation": "Allocation",
    "launch": "Lancement",
    "target_stock": "Stock cible",
    "dc_return": "Retour DC",
    "exclusion": "Exclusion",
    "event": "Événement",
    "override": "Modifié",
}
# Puce pleine = événement d'équipe, cerclée = événement personnel.
EVENT_DOT = {"team": "●", "personal": "○"}
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
        "orders": {},  # Commandes : boutique -> paramètres, résultat et validations (ordre d'ajout)
        "active": None,  # boutique affichée dans la page Commandes
        "nonce": 0,
        "why_sku": None,
        "author": None,
        "taken_over": set(),  # événements personnels d'un collègue repris pour ma session
        "confirmed_blocks": set(),  # SKU bloqués par un événement, blocage confirmé
        "include_ok": False,  # lancements / demandes à inclure confirmés
        "requests_to_close": [],  # demandes boutique à terminer après l'export
        "ev_edit": None,  # Événements : panneau ouvert (id, "new" ou "request")
        "ev_nonce": 0,
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
    ss.confirmed_blocks, ss.include_ok = set(), False
    ss.saved_run_id, ss.why_sku = None, None
    ss.nonce += 1


# État de la commande affichée. Une seule boutique est ouverte à la fois : ses clés vivent
# dans la session (la grille les lit directement), celles des autres boutiques sont rangées
# dans ss.orders[boutique]["state"] et reprises quand on clique sur leur onglet.
ORDER_KEYS = (
    "base",
    "overrides",
    "seen",
    "confirmed_blocks",
    "include_ok",
    "saved_run_id",
    "why_sku",
)


def order_state(boutique: str) -> dict | None:
    """Résultat et validations d'une boutique ajoutée (None tant qu'elle n'est pas calculée)."""
    if boutique == ss.active:
        return {k: ss[k] for k in ORDER_KEYS} if ss.base is not None else None
    slot = ss.orders.get(boutique)
    return slot.get("state") if slot else None


def park_active() -> None:
    """Range l'état de la commande affichée dans son onglet."""
    if ss.active in ss.orders:
        ss.orders[ss.active]["state"] = order_state(ss.active)


def activate(boutique: str | None) -> None:
    """Affiche la commande d'une boutique (onglet) : l'état de l'ancienne est gardé."""
    if boutique == ss.active:
        return
    park_active()
    reset_order()
    ss.active = boutique
    saved = ss.orders[boutique].get("state") if boutique in ss.orders else None
    if saved:
        for k, v in saved.items():
            ss[k] = v
    ss.pop("f_event", None)  # les événements diffèrent d'une boutique à l'autre


def reset_all() -> None:
    reset_order()
    ss.day, ss.day_error = None, None
    ss.orders, ss.active = {}, None


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


@contextmanager
def db_conn() -> Iterator:
    conn = db.connect(workspace().db_path)
    try:
        yield conn
    finally:
        conn.close()


def all_events() -> list[SkuEvent]:
    with db_conn() as conn:
        return event_store.list_events(conn)


def planners() -> list[str]:
    with db_conn() as conn:
        return event_store.list_planners(conn)


def seed_demo_events() -> None:
    """Démo : 3 planners et 4 événements FICTIFS sur B80, créés une seule fois (sans les
    exemples d'allocation, de lancement et de stock cible, montrés dans la démo web)."""
    from app.ingestion.events import DEMO_PLANNERS, example_events

    with db_conn() as conn:
        if event_store.list_planners(conn) or event_store.list_events(conn):
            return
        for name in DEMO_PLANNERS:
            event_store.add_planner(conn, name)
        for e in example_events(business=False):
            event_store.save_event(conn, e, "Démo")


def add_planner_and_select(name: str) -> str | None:
    """Ajoute un prénom à la liste et le choisit. Retourne un message d'erreur sinon."""
    try:
        with db_conn() as conn:
            ss.author = event_store.add_planner(conn, name)
    except ValueError as exc:
        return str(exc)
    return None


def event_context() -> EventContext:
    """Tous les événements + le planner connecté + ceux qu'il a repris (« Reprendre »)."""
    return EventContext(tuple(all_events()), author(), frozenset(ss.taken_over))


def event_cell(line: RecommendationLine) -> str:
    """« ● Festive AOS · ○ Demande B80 » (pleine : Équipe, cerclée : Personnel)."""
    tags = {(t["scope"], t["title"]) for t in event_tags(line)}
    return " · ".join(f"{EVENT_DOT[scope]} {title}" for scope, title in sorted(tags))


# Texte court des événements dans la colonne Raisons (D-047) : « Promo Festive AOS »,
# « Lancement Advent », « Demande B19 », « Arrêt d'envoi »…
EVENT_SHORT = {
    "promotion": "Promo",
    "launch": "Lancement",
    "stop": "Arrêt d'envoi",
    "ban": "Interdiction d'envoi",
    "conversion": "Conversion",
    "urgent": "Urgence",
    "store_request": "Demande",
    "watch": "À surveiller",
    "target_stock": "Stock cible",
}
_REASON_CUT = re.compile(r" : | \(|, | → ")


def short_reason(reason: str) -> str:
    """Une raison en quelques mots : coupée au premier « : », « ( », « , » ou « → »."""
    return _REASON_CUT.split(reason, maxsplit=1)[0].strip()


def event_short_tags(line: RecommendationLine) -> list[str]:
    tags: list[str] = []
    for t in event_tags(line):
        short, title = EVENT_SHORT.get(t["type"], t["type"]), (t.get("title") or "").strip()
        if not title or t["type"] in ("stop", "ban"):
            tag = short
        elif title.lower().startswith(short.lower().split()[0]):
            tag = title
        else:
            tag = f"{short} {title}"
        if tag not in tags:
            tags.append(tag)
    return tags


def short_reasons(line: RecommendationLine, base: RecommendationLine | None = None) -> str:
    """Colonne Raisons (D-047) : source, événements, puis chaque raison en texte court.

    Le détail complet reste dans le panneau « Pourquoi ».
    """
    titles = [t.get("title") or "" for t in event_tags(base or line)]
    # « Événement » et « Modifié » sont déjà dits par le tag de l'événement / « Modifié par … ».
    tags = [] if line.source.value in ("event", "override") else [SOURCE_LABEL[line.source.value]]
    tags += event_short_tags(base or line)
    for r in line.reasons:
        if r.startswith("Source : ") or any(t and t in r for t in titles):  # déjà dit par le tag
            continue
        short = short_reason(r)
        if short and short not in tags:
            tags.append(short)
    return " | ".join(tags)


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


def is_confirmed(sku: str) -> bool:
    """Ligne confirmée par le planner (case « Confirmé », ou blocage d'événement confirmé)."""
    return sku in ss.seen or sku in ss.get("confirmed_blocks", set())


def shown_status(sku: str, base_status: Status) -> str:
    """« Confirmé » si le planner l'a coché, sinon le statut du calcul (D-039)."""
    return "Confirmé" if is_confirmed(sku) else base_status.value


def status_cell(line: RecommendationLine, base: RecommendationLine) -> str:
    """Statut de la ligne + marqueur « modifié » ; une modification ne change pas le statut."""
    label = CONFIRMED if is_confirmed(line.sku) else STATUS_ICON[base.status.value]
    return label + (MODIFIED_MARK if line.qty != base.qty else "")


def make_override(base_line: RecommendationLine, after: int, reason: str = "") -> Override:
    """Modification du planner, appliquée tout de suite (D-038) : rien n'est demandé.

    Gardés automatiquement : quantité d'avant, quantité saisie, planner, heure et le contexte
    déduit de la ligne (événement actif, stock DC insuffisant, SKU dormant, sinon « non
    précisé »). La raison libre reste facultative (panneau « Pourquoi »).
    """
    context, detail = deduce_context(base_line, after)
    return Override(
        sku=base_line.sku,
        qty_before=base_line.qty,
        qty_after=after,
        context=context,
        context_detail=detail,
        reason=reason.strip(),
        author=author() or "Planner non renseigné",
        timestamp=datetime.now(),
    )


def rounding_note(line: RecommendationLine, typed: int) -> str | None:
    """Petit avertissement quand la qty tapée n'est pas un multiple (elle est arrondie)."""
    multiple = int(line.explanation.get("multiple") or 1)
    if multiple <= 1 or typed % multiple == 0:
        return None
    rounded = -(-typed // multiple) * multiple
    return f"{line.sku} : {typed} n'est pas un multiple de {multiple}, arrondi à {rounded}."


def category_of_line(line: RecommendationLine, mapping: dict[str, str]) -> str:
    return category_of(line.product_type, mapping)


def _round1(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


def order_frame(
    lines: list[RecommendationLine],
    base: dict[str, RecommendationLine],
    mapping: dict[str, str],
) -> pd.DataFrame:
    """Une seule grille, colonnes dans l'ordre du planner ; nombres en vrais nombres (tri)."""
    rows = []
    for x in lines:
        b = base.get(x.sku, x)
        e, inp = x.explanation, x.explanation["inputs"]
        sales = inp["sales_total"]
        rows.append(
            {
                "Confirmé": is_confirmed(x.sku),
                "Détail": x.sku == ss.get("why_sku"),
                "Statut": status_cell(x, b),
                "SKU": x.sku,
                "Description": x.description or "",
                "Expected": num(inp["expected_total"]),
                "Available": num(inp.get("available_total")),
                "Ventes": num(sales),
                "Qty proposée": b.qty,
                "Qty finale": x.qty,
                # D-044 / D-046 : stock attendu une fois la commande livrée (Expected du jour
                # + Qty finale) et couverture calculée dessus ; les deux suivent chaque saisie.
                "Expected + commande": num(inp["expected_total"] + x.qty),
                "Couverture": num(_round1(cover_after(x))),
                "Multiple": num(e["multiple"]),
                "Stock DC": num(inp["dc_available"]),
                "Raisons": short_reasons(x, b),
            }
        )
    return pd.DataFrame(rows, columns=ORDER_COLUMNS)


def incoming_of(line: RecommendationLine) -> float | None:
    """Écart Expected − Available = ce qui est en route, pas encore reçu (D-047)."""
    inp = line.explanation["inputs"]
    avail = inp.get("available_total")
    return None if avail is None else float(inp["expected_total"]) - float(avail)


def cover_after(line: RecommendationLine, qty: int | None = None) -> float | None:
    """Couverture (jours) sur Expected + Qty finale ; None sans ventes."""
    e, inp = line.explanation, line.explanation["inputs"]
    sales = inp["sales_total"]
    if not sales:
        return None
    q = line.qty if qty is None else qty
    return float((inp["expected_total"] + q) / sales * e["parameters"]["sales_history_days"])


def cover_target(line: RecommendationLine) -> float | None:
    """Cible de couverture de la ligne : cover café si café et cover café saisi, sinon cover."""
    target = line.explanation.get("cover_applied")
    return None if target is None else float(target)


ORDER_COLUMNS = [
    "Confirmé", "Détail", "Statut", "SKU", "Description", "Expected", "Available", "Ventes",
    "Qty proposée", "Qty finale", "Expected + commande", "Couverture", "Multiple", "Stock DC",
    "Raisons",
]  # fmt: skip

_INT = {"format": "localized"}
# En-têtes abrégés et largeurs serrées pour qu'un écran 13 pouces montre toutes les colonnes
# (barre latérale repliée) ; le nom complet est dans l'aide de la colonne (D-047).
ORDER_COLUMN_CONFIG = {
    "Confirmé": st.column_config.CheckboxColumn(
        "Confirmé",
        width=62,
        pinned=True,
        help="Cocher = ligne vérifiée (statut « Confirmé »). Décocher pour la remettre à traiter.",
    ),  # fmt: skip
    "Détail": st.column_config.CheckboxColumn(
        "Détail", width=54, help="Pourquoi cette quantité ?", pinned=True
    ),
    # Bloc figé à gauche (Confirmé, Détail, Statut, SKU) : le reste défile.
    "Statut": st.column_config.TextColumn(width=118, pinned=True),
    "SKU": st.column_config.TextColumn(width=86, pinned=True),
    "Description": st.column_config.TextColumn(width=150),
    "Expected": st.column_config.NumberColumn(
        "Exp.", width=62, help="Expected : stock attendu du jour (dispo + en route)", **_INT
    ),
    "Available": st.column_config.NumberColumn(
        "Avail.",
        width=62,
        help="Available : stock physiquement en boutique. L'écart avec Expected = en route, "
        "pas encore reçu (détail dans le « Pourquoi »)",
        **_INT,
    ),  # fmt: skip
    "Ventes": st.column_config.NumberColumn("Ventes", width=62, **_INT),
    "Qty proposée": st.column_config.NumberColumn(
        "Prop.", width=62, help="Qty proposée par le calcul", **_INT
    ),
    "Qty finale": st.column_config.NumberColumn(
        "Finale", width=72, min_value=0, step=1, help="Qty finale, modifiable", **_INT
    ),
    "Expected + commande": st.column_config.NumberColumn(
        "Exp.+cmd", width=76, help="Expected + commande : Expected du jour + Qty finale", **_INT
    ),
    "Couverture": st.column_config.NumberColumn(
        "Couv.",
        width=60,
        format="%.1f",
        help="Jours de couverture sur Expected + Qty finale (Qty finale 0 = couverture actuelle). "
        "Rouge : sous la cible saisie · vert : au-dessus. Le détail actuelle → après est dans "
        "le « Pourquoi ».",
    ),  # fmt: skip
    "Multiple": st.column_config.NumberColumn("Mult.", width=54, help="Multiple", **_INT),
    "Stock DC": st.column_config.NumberColumn("DC", width=66, help="Stock DC", **_INT),
    "Raisons": st.column_config.TextColumn(
        width=200, help="Source, événements et raisons en texte court ; détail dans le « Pourquoi »"
    ),
}
EDITABLE_COLUMNS = ("Confirmé", "Détail", "Qty finale")


def _status_style(value: str) -> str:
    for key, (fg, bg) in STATUS_COLORS.items():
        if key in value:
            return f"color: {fg}; background-color: {bg}; font-weight: 600"
    return ""


def _cover_style(cover: float | None, target: float | None) -> str:
    """Couleur discrète de la couverture : rouge sous la cible, vert au-dessus (D-046)."""
    if cover is None or target is None or pd.isna(cover):
        return ""
    return f"color: {COVER_LOW}; font-weight: 600" if cover < target else f"color: {COVER_OK}"


def style_order_frame(df: pd.DataFrame, targets: dict[str, float | None] | None = None):  # noqa: ANN201 - pandas Styler
    """Lignes alternées très légères + pastilles de statut colorées."""
    zebra = df.style.apply(
        lambda row: [f"background-color: {ZEBRA}" if row.name % 2 else ""] * len(row), axis=1
    )
    zebra = zebra.map(_status_style, subset=["Statut"])
    if "Qty finale" not in df.columns:  # liste des lignes à confirmer : pas de Qty finale
        return zebra
    if "Couverture" in df.columns and targets is not None:
        zebra = zebra.apply(
            lambda col: [
                _cover_style(cover, targets.get(sku))
                for cover, sku in zip(col, df["SKU"], strict=True)
            ],
            subset=["Couverture"],
        )
    # Marqueur « modifié » : fond bleu très léger sur la Qty finale qui diffère de la proposée.
    return zebra.apply(
        lambda col: [
            f"background-color: {MODIFIED_BG}; color: #2F5F9E; font-weight: 600"
            if final != proposed
            else ""
            for final, proposed in zip(col, df["Qty proposée"], strict=True)
        ],
        subset=["Qty finale"],
    )


def default_order(
    lines: list[RecommendationLine], base: dict[str, RecommendationLine]
) -> list[RecommendationLine]:
    """Tri par défaut : REVIEW et BLOCKED en haut, puis Qty proposée décroissante.

    Calculé sur le statut et la qty du calcul (jamais sur la qty saisie) : une ligne ne bouge
    pas pendant que le planner tape (D-039).
    """

    def key(x: RecommendationLine) -> tuple[bool, int, str]:
        b = base.get(x.sku, x)
        return (b.status is Status.OK, -b.qty, x.sku)

    return sorted(lines, key=key)


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
    events = e.get("events")
    if events:
        titles = ", ".join(dict.fromkeys(f"« {t['title']} »" for t in events["applied"]))
        steps.append(f"**Événement(s)** {titles} : voir les raisons ci-dessous")
    if e.get("override"):
        o = e["override"]
        when = str(o["timestamp"])[:16].replace("T", " ")
        steps.append(f"**Modifié** par {o['author']} le {when} : {fmt_int(o['qty_before'])} → "
                     f"{fmt_int(o['qty_final'])}" + (f" ({o['reason']})" if o["reason"] else ""))  # fmt: skip
        if o["qty_final"] != o["qty_after"]:
            steps.append(f"Saisie {fmt_int(o['qty_after'])} arrondie au multiple : "
                         f"**{fmt_int(o['qty_final'])}**")  # fmt: skip
        steps.append(f"**Contexte déduit** : {o.get('context_detail') or 'Non précisé'}")
    steps.append(f"**Quantité finale : {fmt_int(line.qty)}**")
    return steps
