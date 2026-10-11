"""Écran 5 — Événements SKU : saisis une fois, appliqués à toutes les commandes.

Deux onglets (Équipe / Mes événements), une seule table triable, un panneau à droite
pour créer ou modifier sans quitter la table. Aucune quantité n'est calculée ici : les
quantités saisies sont arrondies au multiple par le moteur, au moment de la commande.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.domain.events import (
    ALL,
    ALLOWED_EFFECTS,
    EFFECT_LABELS,
    OPEN_END_TYPES,
    PERSONAL_ONLY,
    SCOPE_LABELS,
    STATUS_LABELS,
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
from app.engines.events import ACTIVE, ENDED, applies_to_planner, days_left, time_state
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
    if e.status is not EventStatus.ACTIVE:
        return STATUS_LABELS[e.status]
    state = time_state(e, day)
    if state == ENDED:
        return "Terminé"
    if state == ACTIVE:
        return "Bloqué" if e.effect is EventEffect.BLOCK else "Actif"
    return "À venir"


def _status_group(label: str) -> str:
    return {"Actif": "Actifs", "Bloqué": "Actifs", "À venir": "À venir", "Terminé": "Terminés",
            "Annulé": "Terminés", "Brouillon": "Brouillons"}[label]  # fmt: skip


def _ends_soon(e: SkuEvent, day: date) -> bool:
    left = days_left(e, day)
    return left is not None and 0 <= left <= SOON_DAYS and display_status(e, day) != "Terminé"


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
            "Début": e.start,
            "Fin": e.end,
            "Jours restants": float("nan") if left is None else float(max(left, 0)),
            "Alerte": "Fin proche" if _ends_soon(e, day) else "",
            "Effet": _effect_text(e),
            "Responsable": e.planner,
            "Fichier source": e.source_file,
            "Modifié le": e.updated_at,
            "Tous les SKU": ", ".join(skus),
        })  # fmt: skip
    df = pd.DataFrame(rows, columns=COLUMNS)
    for col in ("Début", "Fin"):
        df[col] = pd.to_datetime(df[col])
    return df


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
    "Début": st.column_config.DateColumn(format="DD/MM/YYYY", width=95),
    "Fin": st.column_config.DateColumn(format="DD/MM/YYYY", width=95,
                                       help="Vide = jusqu'à nouvel ordre / prochaine commande"),
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


def _sku_block(proto: SkuEvent | None, etype: EventType, catalog: dict[str, str]) -> list[EventSku]:
    """Zone de collage + validation immédiate (reconnus / inconnus / doublons)."""
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
    text = st.text_area("SKU concernés *", value=", ".join(proto.sku_codes()) if proto else "",
                        key=_k("skus"), height=78,
                        placeholder="Colle une colonne Excel : virgule, point-virgule, espace ou "
                                    "retour à la ligne")  # fmt: skip
    skus = split_skus(text)
    if not skus:
        return []
    checks = check_skus(skus, catalog)
    known = sum(c.known and not c.duplicate for c in checks)
    unknown = [c.sku for c in checks if not c.known]
    dups = [c.sku for c in checks if c.duplicate]
    parts = [f":green[✓ {known} reconnu(s)]"]
    if unknown:
        parts.append(f":orange[⚠ {len(unknown)} inconnu(s)]")
    if dups:
        parts.append(f":red[{len(dups)} doublon(s) retiré(s)]")
    st.markdown(" · ".join(parts))
    if not catalog:
        st.caption("Dépose les exports du jour pour vérifier les SKU.")
    multiples = {s.sku: s.multiple for s in proto.skus} if proto else {}
    rows = [{"SKU": c.sku, "Description": c.description or "", "État": "doublon" if c.duplicate
             else ("reconnu" if c.known else "inconnu"), "Multiple": multiples.get(c.sku)}
            for c in checks]  # fmt: skip
    table = st.data_editor(
        pd.DataFrame(rows).astype({"Multiple": "Float64"}), key=_k(f"skutab_{hash(text)}"),
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


def _qty_block(
    proto: SkuEvent | None, effect: EventEffect, etype: EventType
) -> tuple[EventQty, ...]:
    if effect not in (EventEffect.SET_QTY, EventEffect.ADD_QTY):
        return ()
    rows = [{"Boutique": "Toutes" if q.boutique == ALL else q.boutique,
             "SKU": "Tous" if q.sku == ALL else q.sku, "Qty": q.qty}
            for q in (proto.quantities if proto else ())]  # fmt: skip
    rows = rows or [{"Boutique": "Toutes", "SKU": "Tous", "Qty": None}]
    label = "Quantité imposée" if effect is EventEffect.SET_QTY else "Quantité ajoutée"
    st.markdown(f"**{label}** par boutique")
    grid = st.data_editor(
        pd.DataFrame(rows).astype({"Qty": "Float64"}), key=_k(f"qty_{effect}"), num_rows="dynamic",
        hide_index=True, width="stretch",
        column_config={
            "Boutique": st.column_config.TextColumn(help="« Toutes » ou un code boutique"),
            "SKU": st.column_config.TextColumn(help="« Tous » ou un SKU de la liste"),
            "Qty": st.column_config.NumberColumn(min_value=0, step=1, format="localized"),
        },
    )  # fmt: skip
    st.caption("Colle des lignes depuis Excel (Boutique, SKU, Qty). La quantité la plus précise "
               "gagne ; elle est arrondie au multiple supérieur au calcul.")  # fmt: skip
    out = []
    for r in grid.to_dict("records"):
        b = str(r["Boutique"] or "").strip().upper()
        s = str(r["SKU"] or "").strip()
        if pd.isna(r["Qty"]) and not s and b in ("", "TOUTES"):
            continue
        if pd.isna(r["Qty"]):
            raise ValueError(f"Quantité vide pour {b or 'Toutes'} / {s or 'Tous'}")
        out.append(EventQty(boutique=ALL if b in ("", "TOUTES", "*") else b,
                            sku=ALL if s.lower() in ("", "tous", "*") else s, qty=int(r["Qty"])))  # fmt: skip
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
        open_end = etype in OPEN_END_TYPES
        end = c2.date_input("Fin" + (" (vide = jusqu'à nouvel ordre)" if open_end else " *"),
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
            else ("jusqu'à la **prochaine commande**" if until_next else "jusqu'à **nouvel ordre**")
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
        statuses = list(EventStatus)
        status = st.selectbox("Statut", statuses, key=_k("status"),
                              index=statuses.index(proto.status if proto else EventStatus.ACTIVE),
                              format_func=lambda x: STATUS_LABELS[x])  # fmt: skip
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


def _duplicate(event: SkuEvent) -> None:
    copy = event.model_copy(update={"id": None, "title": f"{event.title} (copie)",
                                    "status": EventStatus.ACTIVE, "created_by": "",
                                    "created_at": None, "updated_at": None})  # fmt: skip
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
            else (
                "jusqu'à la prochaine commande"
                if event.until_next_order
                else "jusqu'à nouvel ordre"
            )
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
        if c3.button("Terminer", key=_k("done"), width="stretch",
                     disabled=proto.status is EventStatus.DONE):  # fmt: skip
            _set_status(proto, EventStatus.DONE)
        if c4.button("Annuler l'événement", key=_k("cancel"), width="stretch",
                     disabled=proto.status is EventStatus.CANCELLED):  # fmt: skip
            _set_status(proto, EventStatus.CANCELLED)
        _history(proto)


def _editable(event: SkuEvent, me: str) -> bool:
    """Équipe : tout le monde. Personnel : seulement son planner."""
    return event.scope is EventScope.TEAM or (bool(me) and event.planner.lower() == me.lower())


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


# --- Écran --------------------------------------------------------------------------------------


def render() -> None:
    me = author()
    day = run_date()
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
    st.caption("Promotions, lancements, arrêts, interdictions, conversions, urgences et demandes "
               "boutique : saisis une fois, ils flaguent ou bloquent les SKU dans toutes les "
               "commandes tant qu'ils sont actifs. Le planner valide.")  # fmt: skip

    def tab_label(t: str) -> str:
        group = team if t == TEAM_TAB else mine
        n = sum(1 for e in group if display_status(e, day) not in ("Terminé", "Annulé"))
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
