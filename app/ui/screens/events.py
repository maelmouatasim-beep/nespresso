"""Écran 5 — Événements SKU : saisis une fois, appliqués à toutes les commandes.

Deux onglets (Équipe / Mes événements), une seule table triable, un panneau à droite
pour créer ou modifier sans quitter la table. Aucune quantité n'est calculée ici : les
quantités saisies sont arrondies au multiple par le moteur, au moment de la commande.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.domain.events import (
    ALL,
    ALLOWED_EFFECTS,
    EFFECT_LABELS,
    FORM_STATUS_LABELS,
    FORM_STATUSES,
    NO_END,
    PERSONAL_EDITABLE_BY_OTHERS,
    PERSONAL_ONLY,
    QTY_EFFECTS,
    SCOPE_LABELS,
    TYPE_LABELS,
    EventEffect,
    EventQty,
    EventScope,
    EventSku,
    EventStatus,
    EventType,
    SkuEvent,
    default_lead_days,
)
from app.engines.events import (
    ACTIVE_LABEL,
    ENDED_LABEL,
    applies_to_planner,
    can_stop,
    date_status,
    days_left,
)
from app.ingestion.business_events import events_from_business
from app.ingestion.events import (
    build_catalog,
    check_skus,
    events_workbook,
    read_events_workbook,
    split_skus,
)
from app.ingestion.masters import all_boutiques
from app.storage import events as store
from app.ui.common import (
    EVENT_DOT,
    ZEBRA,
    all_events,
    author,
    boutique_label,
    day_data,
    db_conn,
    fmt_date,
    fmt_int,
    planners,
    run_date,
    ss,
    today,
    workspace,
)

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "evenements_sku.xlsx"
TEAM_TAB, MINE_TAB = "Équipe", "Mes événements"
STATUS_FILTERS = ("Actifs", "À venir", "Terminés", "Brouillons", "Tous")
# Couleurs de la charte : Actif vert, À venir ambre, Terminé gris, Bloqué rouge.
EVENT_STATUS_COLORS = {
    "Actif": ("#2A7449", "#E6F2EA"),
    "À venir": ("#94600F", "#FFF4DF"),
    "Bloqué": ("#B23A3A", "#FBE9E7"),
    "Terminé": ("#6B5B4E", "#EFEBE6"),
    "Annulé": ("#6B5B4E", "#EFEBE6"),
    "Brouillon": ("#2F5F9E", "#E8F0FA"),
}
TABLE_HEIGHT = 520
SOON_DAYS = 3


# --- État affiché ---------------------------------------------------------------------


def display_status(e: SkuEvent, day: date) -> str:
    """Statut calculé à partir des dates (D-054), `day` = date du jour réelle."""
    label = date_status(e, day)
    if label == ACTIVE_LABEL and e.effect is EventEffect.BLOCK:
        return "Bloqué"
    return label


def _status_group(label: str) -> str:
    return {"Actif": "Actifs", "Bloqué": "Actifs", "À venir": "À venir", "Terminé": "Terminés",
            "Annulé": "Terminés", "Brouillon": "Brouillons"}[label]  # fmt: skip


def _ends_soon(e: SkuEvent, day: date) -> bool:
    left = days_left(e, day)
    return left is not None and 0 <= left <= SOON_DAYS and display_status(e, day) != ENDED_LABEL


def _targets_text(e: SkuEvent) -> str:
    if e.all_boutiques:
        return "Toutes"
    parts = [f"DC {d}" for d in e.dcs] + list(e.boutiques)
    return ", ".join(parts)


def _effect_text(e: SkuEvent) -> str:
    text = EFFECT_LABELS[e.effect]
    if e.quantities:
        q = e.quantities[0]
        where = "" if q.boutique == ALL else f" {q.boutique}"
        text += f" ({fmt_int(q.qty)}{where}" + (" …)" if len(e.quantities) > 1 else ")")
    if e.effect is EventEffect.REPLACE:
        text += " : " + ", ".join(f"{s.sku}→{s.new_sku}" for s in e.skus[:2])
    return text


def _scope_text(e: SkuEvent) -> str:
    dot = EVENT_DOT[e.scope.value]
    if e.scope is EventScope.PERSONAL:
        return f"{dot} Personnel · {e.planner}"
    return f"{dot} Équipe"


def _counts(events: list[SkuEvent], day: date) -> tuple[int, int]:
    """(actifs, à venir dans les 7 prochains jours)."""
    active = sum(1 for e in events if display_status(e, day) in ("Actif", "Bloqué"))
    week = day + timedelta(days=7)
    soon = sum(1 for e in events if display_status(e, day) == "À venir" and e.active_from <= week)
    return active, soon


# --- Table ------------------------------------------------------------------------------

COLUMNS = ["Ouvrir", "N°", "Statut", "Portée", "Type", "Titre", "SKU", "Boutiques", "Début",
           "Fin", "Jours restants", "Alerte", "Effet", "Responsable", "Fichier source",
           "Modifié le", "Tous les SKU"]  # fmt: skip


def _frame(events: list[SkuEvent], day: date) -> pd.DataFrame:
    rows = []
    for e in events:
        skus = e.sku_codes()
        left = days_left(e, day)
        rows.append({
            "Ouvrir": e.id == ss.ev_edit,
            "N°": e.id,
            "Statut": f"● {display_status(e, day)}",
            "Portée": _scope_text(e),
            "Type": TYPE_LABELS[e.type],
            "Titre": e.title,
            "SKU": f"{len(skus)} : {', '.join(skus[:3])}" + (" …" if len(skus) > 3 else ""),
            "Boutiques": _targets_text(e),
            "Début": fmt_date(e.start),
            "Fin": fmt_date(e.end) if e.end else ("prochaine commande" if e.until_next_order
                                                  else NO_END),  # fmt: skip
            "Jours restants": float("nan") if left is None else float(max(left, 0)),
            "Alerte": "Fin proche" if _ends_soon(e, day) else "",
            "Effet": _effect_text(e),
            "Responsable": e.planner,
            "Fichier source": e.source_file,
            "Modifié le": e.updated_at,
            "Tous les SKU": ", ".join(skus),
        })  # fmt: skip
    return pd.DataFrame(rows, columns=COLUMNS)


COLUMN_CONFIG = {
    "Ouvrir": st.column_config.CheckboxColumn("Ouvrir", width=62, pinned=True,
                                              help="Ouvrir dans le panneau de droite"),
    "N°": st.column_config.NumberColumn(width=48),
    "Statut": st.column_config.TextColumn(width=105),
    "Portée": st.column_config.TextColumn(width=150),
    "Type": st.column_config.TextColumn(width=140),
    "Titre": st.column_config.TextColumn(width=200),
    "SKU": st.column_config.TextColumn(width=190, help="Nombre de SKU et les 3 premiers ; "
                                       "la liste complète est dans « Tous les SKU »"),
    "Boutiques": st.column_config.TextColumn(width=110),
    "Début": st.column_config.TextColumn(width=95),
    "Fin": st.column_config.TextColumn(width=105, help="« sans fin » : effectif à partir du "
                                       "début, jusqu'à ce qu'il soit terminé"),
    "Jours restants": st.column_config.NumberColumn("Restant (j)", width=85, format="%d"),
    "Alerte": st.column_config.TextColumn(width=90),
    "Effet": st.column_config.TextColumn(width=170),
    "Responsable": st.column_config.TextColumn(width=105),
    "Fichier source": st.column_config.TextColumn(width=180),
    "Modifié le": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm", width=130),
    "Tous les SKU": st.column_config.TextColumn(width=320),
}  # fmt: skip


def _style(df: pd.DataFrame):  # noqa: ANN202 - pandas Styler
    def status(value: str) -> str:
        for key, (fg, bg) in EVENT_STATUS_COLORS.items():
            if key in str(value):
                return f"color: {fg}; background-color: {bg}; font-weight: 600"
        return ""

    def soon(value: str) -> str:
        return "color: #94600F; font-weight: 600" if value else ""

    zebra = df.style.apply(
        lambda row: [f"background-color: {ZEBRA}" if row.name % 2 else ""] * len(row), axis=1
    )
    return zebra.map(status, subset=["Statut"]).map(soon, subset=["Alerte"])


def _sort_key(e: SkuEvent) -> tuple:
    return (e.end is None, e.end or date.max, e.start, e.id or 0)


# --- Filtres -----------------------------------------------------------------------------


def _visible(events: list[SkuEvent], tab: str) -> list[SkuEvent]:
    if tab == TEAM_TAB:
        return [e for e in events if e.scope is EventScope.TEAM]
    return [e for e in events if e.scope is EventScope.PERSONAL]


def _filters(events: list[SkuEvent], tab: str, day: date, me: str, boutiques: list[str],
             search: str) -> list[SkuEvent]:  # fmt: skip
    by_status = {s: 0 for s in STATUS_FILTERS}
    for e in events:
        by_status[_status_group(display_status(e, day))] += 1
        by_status["Tous"] += 1
    c1, c2, c3 = st.columns([3.6, 1.3, 1.3], vertical_alignment="bottom")
    status = c1.segmented_control("Statut", STATUS_FILTERS, default="Actifs", key=f"evs_status_{tab}",
                                  format_func=lambda s: f"{s} ({by_status[s]})",
                                  label_visibility="collapsed") or "Tous"  # fmt: skip
    boutique = c2.selectbox("Boutique", boutiques, index=None, placeholder="Toutes les boutiques",
                            key=f"evs_btq_{tab}", label_visibility="collapsed")  # fmt: skip
    names = sorted({e.planner for e in events if e.planner} | ({me} if me else set()))
    default = names.index(me) if tab == MINE_TAB and me in names else None
    planner = c3.selectbox("Planner", names, index=default, placeholder="Tous les planners",
                           key=f"evs_planner_{tab}", label_visibility="collapsed")  # fmt: skip
    types = [t for t in EventType if tab == MINE_TAB or t not in PERSONAL_ONLY]
    chosen = st.pills("Type", types, selection_mode="multi", key=f"evs_type_{tab}",
                      format_func=lambda t: TYPE_LABELS[t], label_visibility="collapsed")  # fmt: skip
    needle = search.strip().lower()
    out = []
    for e in events:
        if status != "Tous" and _status_group(display_status(e, day)) != status:
            continue
        if chosen and e.type not in chosen:
            continue
        if boutique and not e.targets(boutique, _dc_of(boutique)):
            continue
        if planner and e.planner != planner:
            continue
        if needle:
            hay = " ".join([e.title, e.description, *e.sku_codes(), *e.boutiques, *e.dcs]).lower()
            if needle not in hay:
                continue
        out.append(e)
    return sorted(out, key=_sort_key)


def _dc_of(boutique: str) -> str | None:
    data = day_data()
    return data.masters.dc_mapping.get(boutique) if data else None


# --- Panneau : création / modification ------------------------------------------------------


def _open(target: int | str | None, proto: SkuEvent | None = None) -> None:
    ss.ev_edit, ss.ev_proto = target, proto
    ss.ev_nonce += 1


def _k(name: str) -> str:
    return f"ev{ss.ev_nonce}_{name}"


def _catalog() -> dict[str, str]:
    data = day_data()
    if data is None:
        return {}
    products = ((p.sku, p.description) for p in data.masters.products.values())
    stock = ((x.sku, x.description) for x in data.stock)
    return build_catalog(products, stock)


def _paste_skus(pick_key: str, paste_key: str, catalog: dict[str, str]) -> None:
    """Ajoute les SKU collés à la sélection (texte gardé tel quel, doublons retirés)."""
    pasted = split_skus(ss.get(paste_key, ""))
    if not pasted:
        return
    current = list(ss.get(pick_key, []))
    checks = check_skus([*current, *pasted], catalog)
    kept = [c.sku for c in checks if not c.duplicate]
    dups = sum(c.duplicate for c in checks[len(current) :])
    ss[pick_key] = kept
    ss[paste_key] = ""
    added = len(kept) - len(current)
    ss[_k("skus_note")] = f"{added} SKU ajouté(s)" + (f" · :red[{dups} doublon(s) retiré(s)]"
                                                       if dups else "")  # fmt: skip


def _sku_block(proto: SkuEvent | None, etype: EventType, catalog: dict[str, str]) -> list[EventSku]:
    """Sélection multiple avec recherche (+ collage d'une liste) et validation immédiate."""
    if etype is EventType.CONVERSION:
        rows = [{"Ancien SKU": s.sku, "Nouveau SKU": s.new_sku or ""}
                for s in (proto.skus if proto else ())] or [{"Ancien SKU": "", "Nouveau SKU": ""}]  # fmt: skip
        grid = st.data_editor(pd.DataFrame(rows, dtype=str), key=_k("conv"), num_rows="dynamic",
                              hide_index=True, width="stretch",
                              column_config={c: st.column_config.TextColumn(c) for c in
                                             ("Ancien SKU", "Nouveau SKU")})  # fmt: skip
        st.caption("L'ancien SKU est bloqué ; le nouveau reprend ses ventes et son Expected "
                   "(même moteur que SKU Conversion).")  # fmt: skip
        out = []
        for r in grid.to_dict("records"):
            old, new = str(r["Ancien SKU"] or "").strip(), str(r["Nouveau SKU"] or "").strip()
            if old or new:
                out.append(EventSku(sku=old or "?", new_sku=new or None))
        return out
    pick_key, paste_key = _k("skus_pick"), _k("skus")
    if pick_key not in ss:
        ss[pick_key] = proto.sku_codes() if proto else []
    chosen: list[str] = list(ss[pick_key])
    options = list(dict.fromkeys([*chosen, *sorted(catalog)]))
    skus = st.multiselect(
        "SKU concernés *", options, key=pick_key, accept_new_options=True,
        placeholder="Rechercher un SKU ou une description (plusieurs possibles)",
        format_func=lambda x: f"{x} · {catalog[x]}" if catalog.get(x) else x,
        help="Un événement peut porter autant de SKU que nécessaire : il s'applique à chacun.",
    )  # fmt: skip
    st.text_area("Ou colle une liste (colonne Excel)", key=paste_key, height=68,
                 on_change=_paste_skus, args=(pick_key, paste_key, catalog),
                 placeholder="Virgule, point-virgule, espace ou retour à la ligne : les SKU "
                             "s'ajoutent à la liste au-dessus")  # fmt: skip
    note = ss.pop(_k("skus_note"), "")
    if not skus:
        if note:
            st.caption(note)
        return []
    checks = check_skus(skus, catalog)
    known = sum(c.known for c in checks)
    unknown = [c.sku for c in checks if not c.known]
    parts = [f":green[✓ {known} reconnu(s)]"]
    if unknown:
        parts.append(f":orange[⚠ {len(unknown)} inconnu(s)]")
    if note:
        parts.append(note)
    st.markdown(" · ".join(parts))
    if not catalog:
        st.caption("Dépose les exports du jour pour vérifier les SKU.")
    multiples = {s.sku: s.multiple for s in proto.skus} if proto else {}
    rows = [{"SKU": c.sku, "Description": c.description or "", "État": "doublon" if c.duplicate
             else ("reconnu" if c.known else "inconnu"), "Multiple": multiples.get(c.sku)}
            for c in checks]  # fmt: skip
    table = st.data_editor(
        pd.DataFrame(rows).astype({"Multiple": "Float64"}), key=_k(f"skutab_{hash(tuple(skus))}"),
        hide_index=True, width="stretch", disabled=["SKU", "Description", "État"],
        height=min(36 * (len(rows) + 1) + 3, 220),
        column_config={
            "SKU": st.column_config.TextColumn(width=90),
            "Description": st.column_config.TextColumn(width=150),
            "État": st.column_config.TextColumn(width=70),
            "Multiple": st.column_config.NumberColumn(
                width=70, min_value=1, step=1, help="Facultatif : lancement absent de la Multiple list"
            ),
        },
    )  # fmt: skip
    out, seen = [], set()
    for r in table.to_dict("records"):
        if r["État"] == "doublon" or r["SKU"] in seen:
            continue
        seen.add(r["SKU"])
        m = r["Multiple"]
        out.append(EventSku(sku=r["SKU"], multiple=None if pd.isna(m) else int(m)))
    return out


def _targets_block(proto: SkuEvent | None, etype: EventType, boutiques: list[str],
                   dcs: list[str], names: dict[str, str]) -> tuple[tuple[str, ...], tuple[str, ...]]:  # fmt: skip
    if etype is EventType.STORE_REQUEST:
        current = proto.boutiques[0] if proto and proto.boutiques else None
        b = st.selectbox("Boutique *", boutiques, key=_k("one_btq"), placeholder="Choisir",
                         index=boutiques.index(current) if current in boutiques else None,
                         format_func=lambda x: boutique_label(x, names))  # fmt: skip
        return ((b,) if b else ()), ()
    modes = ("Toutes", "Par DC", "Liste")
    default = (
        "Liste" if proto and proto.boutiques else ("Par DC" if proto and proto.dcs else "Toutes")
    )
    mode = st.segmented_control("Boutiques concernées", modes, default=default,
                                key=_k("btq_mode")) or "Toutes"  # fmt: skip
    if mode == "Par DC":
        chosen = st.multiselect("DC", dcs, default=[d for d in (proto.dcs if proto else ()) if d in dcs],
                                key=_k("dcs"), placeholder="Choisir un ou plusieurs DC")  # fmt: skip
        return (), tuple(chosen)
    if mode == "Liste":
        chosen = st.multiselect("Boutiques", boutiques, key=_k("btqs"), placeholder="Rechercher",
                                default=[b for b in (proto.boutiques if proto else ()) if b in boutiques],
                                format_func=lambda x: boutique_label(x, names))  # fmt: skip
        return tuple(chosen), ()
    return (), ()


# Colonnes en plus de Boutique / SKU / Qty, selon l'effet (D-042).
QTY_LABELS = {
    EventEffect.SET_QTY: ("Quantité imposée", "Qty"),
    EventEffect.ADD_QTY: ("Quantité ajoutée", "Qty"),
    EventEffect.ALLOCATION: ("Allocation officielle : total, déjà envoyé, vagues", "Total"),
    EventEffect.INITIAL_QTY: ("Quantité initiale à avoir au lancement", "Quantité initiale"),
    EventEffect.TARGET: ("Stock min / cible / max", "Cible"),
}
QTY_EXTRA = {
    EventEffect.ALLOCATION: ("Déjà envoyé", "Vagues (%)", "Vague en cours"),
    EventEffect.TARGET: ("Min", "Max"),
}
QTY_HELP = {
    EventEffect.SET_QTY: "La quantité la plus précise gagne ; arrondie au multiple supérieur.",
    EventEffect.ADD_QTY: "La quantité la plus précise gagne ; arrondie au multiple supérieur.",
    EventEffect.ALLOCATION: "Quantité envoyée = total × % cumulé jusqu'à la vague en cours − déjà "
                            "envoyé, arrondie au multiple. Vagues : ex. 75/25 (vide = une vague). "
                            "Jamais écrasée par la prévision.",
    EventEffect.INITIAL_QTY: "Jusqu'au début (date de lancement) : quantité initiale − Expected, "
                             "arrondie au multiple. Ensuite la prévision reprend.",
    EventEffect.TARGET: "Si le stock après commande passe sous le min : quantité = cible − "
                        "Expected, arrondie au multiple. Au-dessus du max : plafonnée.",
}  # fmt: skip


def _num(value: object, what: str, where: str) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"{what} illisible pour {where} : « {text} »") from exc
    if not number.is_integer() or number < 0:
        raise ValueError(f"{what} doit être un entier positif pour {where}")
    return int(number)


def _qty_block(
    proto: SkuEvent | None, effect: EventEffect, etype: EventType
) -> tuple[EventQty, ...]:
    if effect not in QTY_EFFECTS:
        return ()
    label, qty_col = QTY_LABELS[effect]
    extra = QTY_EXTRA.get(effect, ())

    def row_of(q: EventQty) -> dict[str, object]:
        row: dict[str, object] = {"Boutique": "Toutes" if q.boutique == ALL else q.boutique,
                                  "SKU": "Tous" if q.sku == ALL else q.sku, qty_col: q.qty}  # fmt: skip
        values = {"Déjà envoyé": q.already_sent, "Vagues (%)": "/".join(map(str, q.waves)),
                  "Vague en cours": q.current_wave, "Min": q.min_qty, "Max": q.max_qty}  # fmt: skip
        return {**row, **{c: values[c] for c in extra}}

    rows = [row_of(q) for q in (proto.quantities if proto else ())]
    blank: dict[str, object] = {"Boutique": "Toutes", "SKU": "Tous", qty_col: None}
    if effect is EventEffect.ALLOCATION:
        blank |= {"Déjà envoyé": 0, "Vagues (%)": "100", "Vague en cours": 1}
    elif effect is EventEffect.TARGET:
        blank |= {"Min": None, "Max": None}
    df = pd.DataFrame(rows or [blank])
    numeric = [qty_col, *[c for c in extra if c != "Vagues (%)"]]
    types = {c: "Float64" for c in numeric}
    if "Vagues (%)" in extra:
        types["Vagues (%)"] = "string"
    df = df.astype(types)
    st.markdown(f"**{label}** par boutique")
    config = {
        "Boutique": st.column_config.TextColumn(help="« Toutes » ou un code boutique"),
        "SKU": st.column_config.TextColumn(help="« Tous » ou un SKU de la liste"),
        **{c: st.column_config.NumberColumn(min_value=0, step=1, format="localized")
           for c in numeric},
        "Vagues (%)": st.column_config.TextColumn(help="Pourcentages séparés par /, total 100"),
    }  # fmt: skip
    grid = st.data_editor(df, key=_k(f"qty_{effect}"), num_rows="dynamic", hide_index=True,
                          width="stretch", column_config=config)  # fmt: skip
    st.caption("Colle des lignes depuis Excel (Boutique, SKU, " + ", ".join([qty_col, *extra])
               + "). " + QTY_HELP[effect])  # fmt: skip
    out = []
    for r in grid.to_dict("records"):
        b = str(r["Boutique"] or "").strip().upper()
        s = str(r["SKU"] or "").strip()
        where = f"{b or 'Toutes'} / {s or 'Tous'}"
        q = _num(r[qty_col], qty_col, where)
        if q is None and not s and b in ("", "TOUTES"):
            continue
        if q is None:
            raise ValueError(f"{qty_col} vide pour {where}")
        waves_text = str(r.get("Vagues (%)") or "").strip() if "Vagues (%)" in extra else ""
        if waves_text in ("<NA>", "nan"):
            waves_text = ""
        waves = tuple(_num(w.rstrip("%"), "Vague", where) or 0
                      for w in waves_text.replace(";", "/").split("/") if w.strip())  # fmt: skip
        out.append(EventQty(
            boutique=ALL if b in ("", "TOUTES", "*") else b,
            sku=ALL if s.lower() in ("", "tous", "*") else s, qty=q,
            min_qty=_num(r.get("Min"), "Min", where), max_qty=_num(r.get("Max"), "Max", where),
            already_sent=_num(r.get("Déjà envoyé"), "Déjà envoyé", where) or 0,
            waves=(() if waves == (100,) else waves),
            current_wave=_num(r.get("Vague en cours"), "Vague en cours", where) or 1,
        ))  # fmt: skip
    del etype
    return tuple(out)


def _form(proto: SkuEvent | None, scope_default: EventScope, me: str) -> SkuEvent | None:
    """Champs du panneau. Retourne l'événement construit, ou None (erreurs affichées)."""
    data = day_data()
    names = data.masters.boutique_names if data else {}
    boutiques = all_boutiques(data.masters, data.stock) if data else []
    dcs = sorted(set(data.masters.dc_mapping.values())) if data else []
    scope_labels = list(SCOPE_LABELS.values())
    scope_value = proto.scope if proto else scope_default
    scope_label = st.radio("Portée *", scope_labels, index=scope_labels.index(SCOPE_LABELS[scope_value]),
                           horizontal=True, key=_k("scope"),
                           help="Équipe : appliqué pour tous. Personnel : seulement quand "
                                "c'est toi qui calcules (visible des autres en lecture seule).")  # fmt: skip
    scope = EventScope.TEAM if scope_label == "Équipe" else EventScope.PERSONAL
    types = [t for t in EventType if scope is EventScope.PERSONAL or t not in PERSONAL_ONLY]
    current = proto.type if proto and proto.type in types else types[0]
    etype = st.selectbox("Type *", types, index=types.index(current), key=_k(f"type_{scope}"),
                         format_func=lambda t: TYPE_LABELS[t])  # fmt: skip
    title = st.text_input("Titre *", value=proto.title if proto else "", key=_k("title"),
                          placeholder="ex. Festive AOS Oct 14-21")  # fmt: skip
    description = st.text_area("Description", value=proto.description if proto else "",
                               key=_k("desc"), height=68)  # fmt: skip
    errors: list[str] = []
    skus = _sku_block(proto, etype, _catalog())
    btqs, dc_codes = _targets_block(proto, etype, boutiques, dcs, names)

    c1, c2, c3 = st.columns([1.1, 1.3, 1])
    start = c1.date_input("Début *", value=proto.start if proto else run_date(), key=_k("start"),
                          format="DD/MM/YYYY")  # fmt: skip
    until_next = False
    if etype is EventType.STORE_REQUEST:
        until_next = c2.checkbox("Jusqu'à la prochaine commande",
                                 value=proto.until_next_order if proto else True, key=_k("next"))  # fmt: skip
    end = None
    if not until_next:
        end = c2.date_input("Fin (vide = sans fin)",
                            value=proto.end if proto else None, key=_k(f"end_{etype}"),
                            format="DD/MM/YYYY")  # fmt: skip
    same_type = proto is not None and proto.type is etype
    lead = c3.number_input("Anticip. (j)", min_value=0, max_value=120, step=1,
                           value=proto.lead_days if same_type else default_lead_days(etype),
                           key=_k(f"lead_{etype}"),
                           help="Le SKU est flagué X jours AVANT le début (7 par défaut pour un "
                                "lancement)")  # fmt: skip
    if start:
        until = (
            f"au **{fmt_date(end)}**"
            if end
            else ("jusqu'à la **prochaine commande**" if until_next else "**sans fin**")
        )
        st.caption(f"Actif du **{fmt_date(start - timedelta(days=int(lead)))}** {until} "
                   "(anticipation comprise)")  # fmt: skip

    effects = list(ALLOWED_EFFECTS[etype])
    effect_default = proto.effect if proto and proto.effect in effects else effects[0]
    effect = st.selectbox("Effet sur la commande *", effects, index=effects.index(effect_default),
                          key=_k(f"effect_{etype}"), format_func=lambda x: EFFECT_LABELS[x])  # fmt: skip
    try:
        quantities = _qty_block(proto if proto and proto.effect is effect else None, effect, etype)
    except ValueError as exc:
        errors.append(str(exc))
        quantities = ()
    requested_by, requested_on = "", None
    if etype is EventType.STORE_REQUEST:
        r1, r2 = st.columns(2)
        requested_by = r1.text_input("Qui a demandé", value=proto.requested_by if proto else "",
                                     key=_k("req_by"), placeholder="Prénom ou rôle, pas d'email")  # fmt: skip
        requested_on = r2.date_input("Date de la demande", key=_k("req_on"), format="DD/MM/YYYY",
                                     value=proto.requested_on if proto else run_date())  # fmt: skip
    with st.expander("Suivi (fichier source, responsable, statut)", expanded=proto is None):
        source_file = st.text_input("Fichier source", value=proto.source_file if proto else "",
                                    key=_k("src"), placeholder="ex. FESTIVE AOS OCT14-21.xlsx")  # fmt: skip
        owner = st.text_input("Propriétaire", value=proto.owner if proto else "", key=_k("owner"),
                              placeholder="Équipe ou rôle (pas d'email)")  # fmt: skip
        names_list = planners()
        planner_now = (proto.planner if proto and proto.planner else me) or None
        if planner_now and planner_now not in names_list:
            names_list.append(planner_now)
        planner = st.selectbox("Planner responsable", names_list, key=_k("planner"),
                               index=names_list.index(planner_now) if planner_now else None,
                               placeholder="Choisir")  # fmt: skip
        statuses = list(FORM_STATUSES)
        now_status = proto.status if proto and proto.status in statuses else EventStatus.ACTIVE
        status = st.selectbox("Statut", statuses, key=_k("status"), index=statuses.index(now_status),
                              format_func=lambda x: FORM_STATUS_LABELS[x],
                              help="À venir, actif ou terminé suit les dates. Brouillon et "
                                   "Annulé seulement à la main ; « Terminer » arrête aujourd'hui.")  # fmt: skip
    if errors:
        for err in errors:
            st.error(err)
        return None
    if not skus:
        st.info("Ajoute au moins un SKU.")
        return None
    try:
        return SkuEvent(
            id=proto.id if proto else None, scope=scope, type=etype, title=title.strip(),
            description=description.strip(), skus=tuple(skus), boutiques=btqs, dcs=dc_codes,
            start=start, end=end, lead_days=int(lead), effect=effect, quantities=quantities,
            source_file=source_file.strip(), owner=owner.strip(), planner=planner or "",
            status=status, requested_by=requested_by.strip(), requested_on=requested_on,
            until_next_order=until_next, created_by=proto.created_by if proto else "",
            created_at=proto.created_at if proto else None,
            **_kept_stop(proto, end),
        )  # fmt: skip
    except ValidationError as exc:
        for err in exc.errors():
            st.error(str(err["msg"]).removeprefix("Value error, "))
        return None


def _save(event: SkuEvent, action: str | None = None) -> None:
    me = author()
    if not me:
        st.error("Choisis ton prénom (Planner, barre de gauche) pour enregistrer.")
        return
    with db_conn() as conn:
        saved = store.save_event(conn, event, me, action)
    st.toast(f"Événement « {saved.title} » enregistré", icon=":material/check:")
    _open(saved.id)
    st.rerun()


def _set_status(event: SkuEvent, status: EventStatus) -> None:
    me = author()
    if not me:
        st.error("Choisis ton prénom (Planner, barre de gauche).")
        return
    with db_conn() as conn:
        store.set_status(conn, event.id or 0, status, me)
    _open(event.id)
    st.rerun()


def _kept_stop(proto: SkuEvent | None, end: date | None) -> dict[str, Any]:
    """Un arrêt manuel reste tracé tant que la fin saisie est celle de l'arrêt."""
    if proto is None or proto.id is None or proto.stopped_on is None or end != proto.end:
        return {}
    return {"stopped_by": proto.stopped_by, "stopped_on": proto.stopped_on,
            "end_before_stop": proto.end_before_stop}  # fmt: skip


def _stop(event: SkuEvent, on: bool) -> None:
    """« Terminer » (fin = aujourd'hui) ou « Réactiver » (efface cette fin), tracés."""
    me = author()
    if not me:
        st.error("Choisis ton prénom (Planner, barre de gauche).")
        return
    with db_conn() as conn:
        if on:
            store.stop_event(conn, event.id or 0, me, today())
        else:
            store.reactivate_event(conn, event.id or 0, me)
    ss.base = None  # la commande doit être recalculée
    _open(event.id)
    st.rerun()


def _duplicate(event: SkuEvent) -> None:
    copy = event.model_copy(update={"id": None, "title": f"{event.title} (copie)",
                                    "status": EventStatus.ACTIVE, "created_by": "",
                                    "created_at": None, "updated_at": None, "stopped_by": "",
                                    "stopped_on": None, "end_before_stop": None})  # fmt: skip
    _open("new", copy)


def _take_over(event: SkuEvent, on: bool) -> None:
    me = author()
    if not me:
        ss.ev_msg = "Choisis ton prénom (Planner, barre de gauche) pour reprendre."
        return
    if on:
        ss.taken_over = ss.taken_over | {event.id}
        with db_conn() as conn:
            store.log_takeover(conn, event.id or 0, me)
    else:
        ss.taken_over = ss.taken_over - {event.id}
    ss.base = None  # la commande doit être recalculée avec cet événement


def _history(event: SkuEvent) -> None:
    with db_conn() as conn:
        rows = store.event_history(conn, event.id or 0)
    with st.expander(f"Historique ({len(rows)})"):
        for h in rows:
            what = ", ".join(h["changes"]) if h["changes"] else ""
            st.markdown(f"- {h['at'][:16].replace('T', ' ')} · **{h['author']}** · {h['action']}"
                        + (f" ({what})" if what else ""))  # fmt: skip


def _read_only(event: SkuEvent) -> None:
    st.markdown(f"**{event.title}**  \n{TYPE_LABELS[event.type]} · {_scope_text(event)}")
    st.caption(f"Événement personnel de **{event.planner}** : lecture seule. « Reprendre » "
               "l'applique à tes commandes pendant ta session (tracé).")  # fmt: skip
    if event.description:
        st.markdown(event.description)
    lines = [
        f"SKU : {', '.join(event.sku_codes())}",
        f"Boutiques : {_targets_text(event)}",
        f"Du {fmt_date(event.start)} "
        + (
            f"au {fmt_date(event.end)}"
            if event.end
            else ("jusqu'à la prochaine commande" if event.until_next_order else NO_END)
        ),  # fmt: skip
        f"Effet : {_effect_text(event)}",
    ]
    if event.requested_by:
        lines.append(f"Demandé par : {event.requested_by}"
                     + (f" le {fmt_date(event.requested_on)}" if event.requested_on else ""))  # fmt: skip
    st.markdown("\n".join(f"- {x}" for x in lines))
    if event.id in ss.taken_over:
        st.success("Repris : appliqué à tes commandes.")
        st.button("Ne plus appliquer", key=_k("untake"), on_click=_take_over, args=(event, False))
    else:
        st.button("Reprendre", type="primary", icon=":material/person_check:", key=_k("take"),
                  on_click=_take_over, args=(event, True), width="stretch")  # fmt: skip
    if ss.get("ev_msg"):
        st.error(ss.pop("ev_msg"))
    _history(event)


def _quick_request(me: str) -> None:
    """« + Demande boutique » : boutique, SKU, qty, qui a demandé."""
    data = day_data()
    names = data.masters.boutique_names if data else {}
    boutiques = all_boutiques(data.masters, data.stock) if data else []
    st.markdown("**Demande boutique** (personnelle)")
    b = st.selectbox("Boutique", boutiques, index=None, placeholder="Rechercher", key=_k("q_btq"),
                     format_func=lambda x: boutique_label(x, names))  # fmt: skip
    sku = st.text_input("SKU", key=_k("q_sku"), placeholder="ex. 7005.70").strip()
    if sku:
        check = check_skus([sku], _catalog())[0]
        st.caption(f":green[✓ {check.description or 'reconnu'}]" if check.known
                   else ":orange[⚠ SKU inconnu : il sera quand même ajouté à la commande, en REVIEW]")  # fmt: skip
    qty = st.number_input("Quantité demandée", min_value=1, step=1, value=None, key=_k("q_qty"))
    who = st.text_input("Qui a demandé", key=_k("q_who"), placeholder="Prénom ou rôle, pas d'email")
    effect = st.radio("Effet", [EventEffect.ADD_QTY, EventEffect.SET_QTY], horizontal=True,
                      key=_k("q_effect"), format_func=lambda x: EFFECT_LABELS[x],
                      help="Ajoutée : en plus du besoin calculé. Imposée : remplace la prévision.")  # fmt: skip
    st.caption("Échéance : prochaine commande. Arrondie au multiple au calcul ; la demande se "
               "termine après l'export, avec ta confirmation.")  # fmt: skip
    c1, c2 = st.columns(2)
    if c1.button("Enregistrer", type="primary", key=_k("q_save"), width="stretch"):
        missing = [
            n for n, v in (("la boutique", b), ("le SKU", sku), ("la quantité", qty)) if not v
        ]
        if missing:
            st.error("Il manque " + ", ".join(missing) + ".")
            return
        if not me:
            st.error("Choisis ton prénom (Planner, barre de gauche).")
            return
        try:
            event = SkuEvent(
                scope=EventScope.PERSONAL, type=EventType.STORE_REQUEST, title=f"Demande {b}",
                skus=(EventSku(sku=sku),), boutiques=(b,), start=run_date(), end=None,
                until_next_order=True, effect=effect, quantities=(EventQty(qty=int(qty)),),
                planner=me, requested_by=who.strip(), requested_on=run_date(),
            )  # fmt: skip
        except ValidationError as exc:
            st.error("; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()))
            return
        _save(event, "créé (demande boutique)")
    c2.button("Fermer", key=_k("q_close"), on_click=_open, args=(None,), width="stretch")


def _panel(events: dict[int, SkuEvent], tab: str, me: str) -> None:
    target = ss.ev_edit
    if target == "request":
        _quick_request(me)
        return
    proto = ss.get("ev_proto") if target == "new" else events.get(target)
    t1, t2 = st.columns([5, 1])
    t1.markdown("**Nouvel événement**" if target == "new" else f"**Événement n° {target}**")
    t2.button("", icon=":material/close:", key=_k("close"), on_click=_open, args=(None,),
              help="Fermer")  # fmt: skip
    if proto is not None and target != "new" and not _editable(proto, me):
        _read_only(proto)
        return
    scope_default = EventScope.TEAM if tab == TEAM_TAB else EventScope.PERSONAL
    built = _form(proto, scope_default, me)
    c1, c2 = st.columns(2)
    if c1.button("Enregistrer", type="primary", key=_k("save"), width="stretch",
                 disabled=built is None):  # fmt: skip
        assert built is not None
        _save(built)
    if c2.button("Dupliquer", key=_k("dup"), width="stretch", disabled=built is None and proto is None,
                 help="Recréer une promo l'an prochain ou la copier vers une autre boutique"):  # fmt: skip
        _duplicate(built or proto)  # type: ignore[arg-type]
        st.rerun()
    if proto is not None and proto.id is not None:
        c3, c4 = st.columns(2)
        if proto.stopped_on is not None:
            st.caption(f"Terminé manuellement par **{proto.stopped_by or '—'}** le "
                       f"**{fmt_date(proto.stopped_on)}**.")  # fmt: skip
            if c3.button("Réactiver", key=_k("reactivate"), width="stretch",
                         help="Efface la fin posée par « Terminer » (remet la fin prévue, ou "
                              "sans fin)"):  # fmt: skip
                _stop(proto, False)
        else:
            why = can_stop(proto, today())
            if c3.button("Terminer", key=_k("done"), width="stretch", disabled=why is not None,
                         help=why or "Fin = aujourd'hui, noté dans l'historique"):  # fmt: skip
                _stop(proto, True)
        if c4.button("Annuler l'événement", key=_k("cancel"), width="stretch",
                     disabled=proto.status is EventStatus.CANCELLED):  # fmt: skip
            _set_status(proto, EventStatus.CANCELLED)
        _history(proto)


def _editable(event: SkuEvent, me: str) -> bool:
    """Équipe : tout le monde. Personnel : son planner (et les autres si le réglage le permet)."""
    if event.scope is EventScope.TEAM or PERSONAL_EDITABLE_BY_OTHERS:
        return True
    return bool(me) and event.planner.lower() == me.lower()


# --- Import / export Excel --------------------------------------------------------------------


def _excel_block(events: list[SkuEvent], me: str) -> None:
    with st.expander("Import / export Excel (template evenements_sku.xlsx)"):
        st.caption("Pour saisir en masse : remplis le template (exemples fictifs à remplacer), "
                   "puis importe-le. La Bible n'est jamais lue par l'outil.")  # fmt: skip
        c1, c2 = st.columns(2)
        c1.download_button("Télécharger le template", data=TEMPLATE.read_bytes(),
                           file_name="evenements_sku.xlsx", key="ev_template", width="stretch",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")  # fmt: skip
        c2.download_button("Exporter les événements", data=events_workbook(events),
                           file_name=f"evenements_sku_{run_date():%Y-%m-%d}.xlsx", key="ev_export",
                           width="stretch",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")  # fmt: skip
        up = st.file_uploader("Importer un classeur", type=["xlsx"], key="ev_upload")
        if up is None:
            return
        result = read_events_workbook(up.getvalue(), up.name, me or "import")
        for p in result.problems:
            st.error(f"Ligne incomplète, non importée : {p}")
        for w in result.warnings:
            st.warning(w)
        if not result.events:
            st.info("Aucun événement complet dans ce fichier.")
            return
        st.success(f"{len(result.events)} événement(s) prêt(s) à importer.")
        if st.button(f"Importer {len(result.events)} événement(s)", type="primary", key="ev_import",
                     disabled=not me):  # fmt: skip
            known = {e.id for e in events}
            with db_conn() as conn:
                for e in result.events:
                    e = e if e.id in known else e.model_copy(update={"id": None})
                    store.save_event(conn, e, me, "importé")
            st.toast(f"{len(result.events)} événement(s) importé(s)")
            st.rerun()
        if not me:
            st.caption("Choisis ton prénom (Planner) pour importer.")


# --- Anciens fichiers allocations, lancements, stocks cibles (D-042) ------------------------------


def _legacy_block(me: str) -> None:
    """Les anciens fichiers comptent encore tant qu'ils ne sont pas convertis : on le dit."""
    ws = workspace()
    legacy, files, reports = ws.legacy_business()
    if not files:
        return
    counts = [(len(legacy.allocations), "allocation(s)"), (len(legacy.launches), "lancement(s)"),
              (len(legacy.targets), "stock(s) cible(s)")]  # fmt: skip
    what = ", ".join(f"{n} {label}" for n, label in counts if n)
    with st.container(border=True, key="ev_legacy"):
        st.warning(f"Anciens fichiers encore utilisés par les commandes : {what or 'aucune ligne'} "
                   f"({', '.join(f.name for f in files)}). Convertis-les en événements SKU : "
                   "mêmes quantités, et les fichiers sont rangés dans masters/_archives.")  # fmt: skip
        for rep in reports:
            for issue in rep.issues:
                st.caption(f"{rep.source} : {issue.message}")
        if st.button("Convertir en événements", type="primary", key="ev_migrate", disabled=not me):
            try:
                created = events_from_business(legacy.allocations, legacy.launches, legacy.targets,
                                               author=me, today=run_date(),
                                               source=", ".join(f.name for f in files))  # fmt: skip
            except (ValueError, ValidationError) as exc:
                st.error(f"Conversion impossible, rien n'est changé : {exc}")
                return
            with db_conn() as conn:
                for e in created:
                    store.save_event(conn, e, me, "créé depuis un ancien fichier")
            ws.archive_legacy_business(me, len(created))
            ss.day, ss.day_error = None, None
            st.toast(f"{len(created)} événement(s) créé(s)")
            st.rerun()
        if not me:
            st.caption("Choisis ton prénom (Planner, barre de gauche) pour convertir.")


# --- Écran --------------------------------------------------------------------------------------


def render() -> None:
    me = author()
    day = today()
    events = all_events()
    by_id = {e.id: e for e in events if e.id is not None}
    team = [e for e in events if e.scope is EventScope.TEAM]
    mine = [e for e in events if e.scope is EventScope.PERSONAL and me and e.planner == me]
    applied = [e for e in events if applies_to_planner(e, me, ss.taken_over)]
    active, soon = _counts(applied, day)

    h1, h2 = st.columns([3, 2], vertical_alignment="bottom")
    h1.header("Événements SKU")
    h2.markdown(f"<div style='text-align:right'><b>{active}</b> actif(s), <b>{soon}</b> à venir "
                f"cette semaine · au {fmt_date(day)}</div>", unsafe_allow_html=True)  # fmt: skip
    st.caption("Promotions et allocations, lancements, stocks cibles, arrêts, interdictions, "
               "conversions, urgences et demandes boutique : saisis une fois, ils s'appliquent à "
               "toutes les commandes tant qu'ils sont actifs. Le planner valide.")  # fmt: skip
    _legacy_block(me)

    def tab_label(t: str) -> str:
        group = team if t == TEAM_TAB else mine
        n = sum(1 for e in group if display_status(e, day) not in (ENDED_LABEL, "Annulé"))
        return f"{t} ({n})"

    tab = st.segmented_control("Onglet", (TEAM_TAB, MINE_TAB), default=TEAM_TAB, key="ev_tab",
                               format_func=tab_label, label_visibility="collapsed",
                               on_change=_open, args=(None,)) or TEAM_TAB  # fmt: skip
    b1, b2, b3 = st.columns([1.3, 1.3, 4], vertical_alignment="bottom")
    b1.button("Nouvel événement", icon=":material/add:", type="primary", key="ev_new",
              on_click=_open, args=("new",), width="stretch")  # fmt: skip
    if tab == MINE_TAB:
        b2.button("Demande boutique", icon=":material/storefront:", key="ev_request",
                  on_click=_open, args=("request",), width="stretch")  # fmt: skip
    search = b3.text_input("Recherche", placeholder="Rechercher un titre, un SKU ou une boutique",
                           key=f"evs_search_{tab}", label_visibility="collapsed")  # fmt: skip
    if tab == MINE_TAB and not me:
        st.info("Choisis ton prénom (Planner, barre de gauche) pour voir tes événements.")
    data = day_data()
    boutiques = all_boutiques(data.masters, data.stock) if data else []
    shown = _filters(_visible(events, tab), tab, day, me, boutiques, search)

    side = ss.ev_edit is not None
    table, panel = st.columns([2.5, 1.5]) if side else (st.container(), None)
    with table:
        df = _frame(shown, day)
        edited = st.data_editor(_style(df), key=f"ev_grid_{tab}_{ss.ev_nonce}", hide_index=True,
                                width="stretch", height=min(TABLE_HEIGHT, 35 * (len(df) + 1) + 4),
                                column_config=COLUMN_CONFIG,
                                disabled=[c for c in df.columns if c != "Ouvrir"])  # fmt: skip
        st.caption(f"{len(shown)} événement(s) · ● Équipe ○ Personnel · tri par date de fin · "
                   "clic sur un en-tête pour trier · case « Ouvrir » pour le panneau")  # fmt: skip
    checked = [int(r["N°"]) for r in edited.to_dict("records") if r["Ouvrir"]]
    new_open = next((i for i in checked if i != ss.ev_edit), None)
    if new_open is not None:
        _open(new_open)
        st.rerun()
    elif (
        isinstance(ss.ev_edit, int)
        and ss.ev_edit not in checked
        and ss.ev_edit in {e.id for e in shown}
    ):
        _open(None)
        st.rerun()
    if panel is not None:
        with panel, st.container(border=True, key="ev_panel"):
            _panel(by_id, tab, me)
    _excel_block(events, me)
