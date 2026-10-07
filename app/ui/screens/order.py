"""Écran 2 — Commande boutique : paramètres, tuiles, une seule grille, panneau « Pourquoi »."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.domain.models import PlanningParameters, RecommendationLine, Status
from app.engines.categories import CATEGORIES
from app.engines.overrides import OverrideError
from app.engines.pallets import MACHINES, OL, OTHERS, VL
from app.engines.schedule import boutiques_ordering_on
from app.ingestion.masters import all_boutiques, build_inputs_for_boutique
from app.services.planning import run_planning, with_overrides
from app.ui.common import (
    CATEGORY_BY_LABEL,
    DEPOT,
    EDITABLE_COLUMNS,
    EXPORT,
    MODE_LABEL,
    MODES,
    MODIFIED_MD,
    ORDER_COLUMN_CONFIG,
    SOURCE_LABEL,
    STATUS_MD,
    author,
    boutique_label,
    category_of_line,
    current,
    day_data,
    default_order,
    explain_steps,
    fmt_1,
    fmt_date,
    fmt_int,
    fr_day,
    go,
    make_override,
    order_frame,
    reset_order,
    run_date,
    ss,
    style_order_frame,
)

CATEGORY_FILTERS = ("Tous", *CATEGORIES)
STATUS_FILTERS = ("Tous", "OK", "REVIEW", "BLOCKED", "Modifié")
TABLE_HEIGHT = 600
FALLBACK_WEEKS_DEFAULT = 4


def compute_order(boutique: str, delivery: date, cover: float, coffee: float | None,
                  history: int, mode_label: str, fallback_weeks: int, codes: list[str] | None) -> None:  # fmt: skip
    data = day_data()
    assert data is not None
    params = PlanningParameters(boutique=boutique, run_date=run_date(), delivery_date=delivery,
                                cover_days=cover, coffee_cover_days=coffee)  # fmt: skip
    inputs, dc = build_inputs_for_boutique(
        boutique, data.stock, data.movements, data.masters.products, data.masters.conversions,
        data.masters.dc_mapping, included_movement_codes=codes, sources=data.source_hashes,
        history_days=history, fallback_weeks=fallback_weeks,
        portfolio=data.masters.portfolio.get(boutique, {}), exclusions=data.exclusions,
    )  # fmt: skip
    reset_order()
    ss.base = run_planning(inputs, params, MODES[mode_label], dc=dc, business_inputs=data.business)


def open_result(result) -> None:  # noqa: ANN001 - PlanningResult déjà calculé (onglet Journée)
    reset_order()
    ss.base = result
    ss.p_boutique = result.params.boutique
    go("2. Commande boutique")


def _set_delivery(day: date) -> None:
    ss.p_delivery = day


def _params_row(data) -> None:  # noqa: ANN001
    names = data.masters.boutique_names
    boutiques = all_boutiques(data.masters, data.stock)
    cols = st.columns([2.2, 1.25, 0.85, 0.85, 0.85, 1.05, 0.65, 0.9], vertical_alignment="bottom")
    boutique = cols[0].selectbox("Boutique", boutiques, index=None, placeholder="Rechercher une boutique",
                                 format_func=lambda b: boutique_label(b, names), key="p_boutique")  # fmt: skip
    delivery = cols[1].date_input("Livraison", value=None, min_value=run_date(), key="p_delivery",
                                  format="DD/MM/YYYY")  # fmt: skip
    cover = cols[2].number_input("Cover (j)", min_value=0.5, max_value=60.0, step=0.5, value=None,
                                 key="p_cover", format="%.1f")  # fmt: skip
    coffee = cols[3].number_input("Cover café", min_value=0.5, max_value=60.0, step=0.5, value=None, format="%.1f",
                                  key="p_coffee", help="Facultatif : cover propre aux cafés (type C)")  # fmt: skip
    history = cols[4].number_input("Historique (j)", min_value=1, max_value=56, value=7, step=1,
                                   key="p_hist")  # fmt: skip
    mode = cols[5].selectbox("Mode", list(MODES), key="p_mode")
    with cols[6].popover(":material/tune:", help="Options avancées", width="stretch"):
        weeks = st.number_input("Semaines de secours (SKU dormants)", min_value=1, max_value=12,
                                value=FALLBACK_WEEKS_DEFAULT, step=1, key="p_fallback")  # fmt: skip
        all_codes = sorted({m.movement_code for m in data.movements if m.location == boutique})
        codes = st.multiselect("Codes de mouvement comptés", all_codes, default=all_codes, placeholder="Aucun",
                               key=f"p_codes_{boutique}")  # fmt: skip
    run = cols[7].button("Calculer", type="primary", key="compute", width="stretch")

    if boutique:
        slot = {
            d.boutique: d for d in boutiques_ordering_on(data.masters.schedule, run_date())
        }.get(boutique)
        if slot and slot.delivery_date != delivery:
            st.button(f"Livraison selon le Schedule : {fr_day(slot.delivery_date)}",
                      on_click=_set_delivery, args=(slot.delivery_date,), key="use_schedule")  # fmt: skip
        elif not slot and data.masters.schedule:
            st.caption(
                f"Pas de commande prévue au Schedule le {fr_day(run_date())} pour cette boutique."
            )

    if run:
        missing = [n for n, v in (("la boutique", boutique), ("la date de livraison", delivery),
                                  ("le cover", cover)) if not v]  # fmt: skip
        if missing:
            st.error("Il manque " + ", ".join(missing) + ".")
            return
        try:
            compute_order(boutique, delivery, cover, coffee, int(history), mode, int(weeks),
                          None if set(codes) == set(all_codes) else codes)  # fmt: skip
        except ValidationError as exc:
            st.error("; ".join(e["msg"] for e in exc.errors()))
        except ValueError as exc:
            st.error(f"Calcul impossible : {exc}")


def _tiles(res) -> None:  # noqa: ANN001
    s, p = res.summary, res.pallets
    cols = st.columns(5)
    cols[0].metric("Unités", fmt_int(s.units_total))
    cols[1].metric("Palettes estimées", fmt_1(p.total_pallets))
    cols[1].caption(f"VL {fmt_1(p.pallets[VL])} · OL {fmt_1(p.pallets[OL])} · "
                    f"Mach. {fmt_1(p.pallets[MACHINES])} · Autres {fmt_1(p.pallets[OTHERS])}")  # fmt: skip
    cols[2].metric(STATUS_MD["OK"], fmt_int(s.by_status["OK"]))
    cols[3].metric(STATUS_MD["REVIEW"], fmt_int(s.by_status["REVIEW"]))
    cols[4].metric(STATUS_MD["BLOCKED"], fmt_int(s.by_status["BLOCKED"]))


def _matches(x: RecommendationLine, cat: str, status: str, qty_only: bool, search: str,
             mapping: dict[str, str]) -> bool:  # fmt: skip
    if cat != "Tous" and category_of_line(x, mapping) != cat:
        return False
    modified = x.sku in ss.overrides
    if status == "Modifié" and not modified:
        return False
    if status in ("OK", "REVIEW", "BLOCKED") and (modified or x.status.value != status):
        return False
    if qty_only and x.qty <= 0:
        return False
    return not search or search in x.sku.lower() or search in (x.description or "").lower()


def _filters(
    lines: list[RecommendationLine], mapping: dict[str, str]
) -> tuple[str, str, bool, str]:
    """Barre de filtres : catégorie, statut, Qty > 0, recherche (aucun recalcul)."""
    by_cat = {c: 0 for c in CATEGORY_FILTERS}
    by_status = {s: 0 for s in STATUS_FILTERS}
    for x in lines:
        by_cat["Tous"] += 1
        by_cat[category_of_line(x, mapping)] += 1
        by_status["Tous"] += 1
        by_status["Modifié" if x.sku in ss.overrides else x.status.value] += 1
    c1, c2 = st.columns([5.2, 1], vertical_alignment="bottom")
    cat = c1.segmented_control("Catégorie", CATEGORY_FILTERS, default="Tous", key="f_cat",
                               format_func=lambda c: f"{c} ({by_cat[c]})") or "Tous"  # fmt: skip
    qty_only = c2.checkbox("Qty > 0 seulement", key="f_qty")
    c3, c4, c5 = st.columns([4.4, 1.5, 1.9], vertical_alignment="bottom")
    status = c3.segmented_control("Statut", STATUS_FILTERS, default="Tous", key="f_status",
                                  format_func=lambda c: f"{c} ({by_status[c]})") or "Tous"  # fmt: skip
    search = c4.text_input("Recherche", placeholder="Rechercher un SKU ou une description",
                           key="f_search", label_visibility="collapsed").strip().lower()  # fmt: skip
    if c5.button("Accepter toutes les lignes OK", icon=":material/done_all:", key="accept_ok",
                 width="stretch"):  # fmt: skip
        ss.seen |= {x.sku for x in lines if x.status is Status.OK and x.sku not in ss.overrides}
        ss.nonce += 1
        st.rerun()
    return cat, status, qty_only, search


def _process_edits(edited: pd.DataFrame, lines: dict[str, RecommendationLine]) -> bool:
    """Qty finale saisie → en attente de justification ; Détail → panneau. True = rerun."""
    checked = [r["SKU"] for r in edited.to_dict("records") if r["Détail"]]
    new_why = next((s for s in checked if s != ss.why_sku), None)
    rerun = False
    if new_why:
        ss.why_sku, rerun = new_why, True
    elif ss.why_sku in lines and ss.why_sku not in checked:
        ss.why_sku, rerun = None, True
    for row in edited.to_dict("records"):
        sku, qty = row["SKU"], row["Qty finale"]
        if pd.isna(qty):
            continue
        if int(qty) != lines[sku].qty:
            rerun |= ss.pending.get(sku) != int(qty)
            ss.pending[sku] = int(qty)
        elif sku in ss.pending:
            ss.pending.pop(sku)
            rerun = True
    return rerun


def _apply_overrides(new: dict) -> None:
    data = day_data()
    try:
        with_overrides(ss.base, new.values(), data.masters.products)
    except OverrideError as exc:
        st.error(str(exc))
        return
    ss.overrides, ss.saved_run_id = new, None
    ss.nonce += 1
    st.rerun()


def _justify_block(res) -> None:  # noqa: ANN001
    base_qty = {x.sku: x.qty for x in res.base_lines}
    lines = {x.sku: x for x in res.lines}
    st.markdown(f"**Modifications à justifier ({len(ss.pending)})**")
    cats = list(CATEGORY_BY_LABEL)
    for sku, qty in ss.pending.items():
        st.markdown(f"`{sku}` {lines[sku].description or ''} · "
                    f"{fmt_int(lines[sku].qty)} → **{fmt_int(qty)}**")  # fmt: skip
        c = st.columns([1, 1.4])
        c[0].selectbox("Catégorie", cats, index=None, key=f"cat_{sku}", placeholder="Catégorie",
                       label_visibility="collapsed")  # fmt: skip
        c[1].text_input(
            "Raison", key=f"reason_{sku}", placeholder="Raison", label_visibility="collapsed"
        )
    b1, b2 = st.columns(2)
    if b1.button("Valider", type="primary", key="validate_pending", width="stretch"):
        if not author():
            st.error("Indique ton nom dans la barre de gauche.")
            return
        errors, new = [], dict(ss.overrides)
        for sku, qty in ss.pending.items():
            cat, reason = ss.get(f"cat_{sku}"), (ss.get(f"reason_{sku}") or "").strip()
            if qty == base_qty[sku]:
                new.pop(sku, None)
                continue
            if not cat or len(reason) < 3:
                errors.append(sku)
                continue
            new[sku] = make_override(sku, base_qty[sku], qty, cat, reason)
        if errors:
            st.error("Catégorie et raison obligatoires pour : " + ", ".join(errors))
            return
        ss.pending = {}
        _apply_overrides(new)
    if b2.button("Annuler", key="cancel_pending", width="stretch"):
        ss.pending = {}
        ss.nonce += 1
        st.rerun()


def _close_why() -> None:
    ss.why_sku = None
    ss.nonce += 1


def _toggle_seen(sku: str) -> None:
    (ss.seen.discard if sku in ss.seen else ss.seen.add)(sku)
    ss.nonce += 1


def _why_panel(res, sku: str, mapping: dict[str, str]) -> None:  # noqa: ANN001
    line = next((x for x in res.lines if x.sku == sku), None)
    if line is None:
        return
    base_qty = {x.sku: x.qty for x in res.base_lines}
    e, inp = line.explanation, line.explanation["inputs"]
    t1, t2 = st.columns([5, 1], vertical_alignment="top")
    t1.markdown(
        f"**Pourquoi cette quantité ?**  \n`{sku}` {line.description or 'sans description'}"
    )
    t2.button("", icon=":material/close:", key="why_close", on_click=_close_why, help="Fermer")
    status = MODIFIED_MD if sku in ss.overrides else STATUS_MD[line.status.value]
    st.caption(f"{status} · {category_of_line(line, mapping)} · source : "
               f"{SOURCE_LABEL[line.source.value]} · multiple {fmt_int(e['multiple'])}")  # fmt: skip
    st.markdown("\n".join(f"{i}. {step}" for i, step in enumerate(explain_steps(line), start=1)))
    dc = inp["dc_available"]
    st.markdown(f"Stock DC : **{fmt_int(dc) if dc is not None else 'absent'}** · "
                f"stock projeté à la livraison : **{fmt_int(e['projected_stock_at_delivery'])}**")  # fmt: skip
    if line.reasons:
        st.markdown("\n".join(f"- {r}" for r in line.reasons))
    st.button("Retirer la validation" if sku in ss.seen else "Marquer comme vérifiée",
              icon=":material/undo:" if sku in ss.seen else ":material/check:",
              key="why_seen", on_click=_toggle_seen, args=(sku,), width="stretch")  # fmt: skip
    st.markdown("**Modifier la quantité**")
    qty = st.number_input("Qty finale", min_value=0, step=int(e["multiple"] or 1),
                          value=int(line.qty), key=f"dlg_qty_{sku}")  # fmt: skip
    cat = st.selectbox("Catégorie", list(CATEGORY_BY_LABEL), index=None, key=f"dlg_cat_{sku}",
                       placeholder="Choisir")  # fmt: skip
    reason = st.text_input("Raison", key=f"dlg_reason_{sku}")
    if st.button("Enregistrer", type="primary", key="dlg_save", width="stretch"):
        if not author():
            st.error("Indique ton nom dans la barre de gauche.")
        elif qty == base_qty[sku]:
            new = dict(ss.overrides)
            new.pop(sku, None)
            ss.pending.pop(sku, None)
            _apply_overrides(new)
        elif not cat or len(reason.strip()) < 3:
            st.error("Catégorie et raison obligatoires.")
        else:
            new = dict(ss.overrides)
            new[sku] = make_override(sku, base_qty[sku], int(qty), cat, reason.strip())
            ss.pending.pop(sku, None)
            _apply_overrides(new)


def render() -> None:
    data = day_data()
    if data is None:
        st.header("Commande boutique")
        st.info("Dépose d'abord les exports du jour.")
        st.button("Aller au dépôt du jour", on_click=go, args=(DEPOT,))
        return
    _params_row(data)
    res = current()
    if res is None:
        return
    p = res.params
    names, mapping = data.masters.boutique_names, data.categories
    st.markdown(
        f"**{boutique_label(p.boutique, names)}** · DC {res.dc} · livraison **{fmt_date(p.delivery_date)}** · "
        f"cover {fmt_1(p.cover_days)} j"
        + (f" (café {fmt_1(p.coffee_cover_days)} j)" if p.coffee_cover_days else "")
        + f" · historique {res.history_days} j · {MODE_LABEL[res.mode.value]}"
    )
    _tiles(res)
    for w in res.rule_warnings:
        st.warning(w)

    cat, status, qty_only, search = _filters(res.lines, mapping)
    base_qty = {x.sku: x.qty for x in res.base_lines}
    shown = default_order(
        [x for x in res.lines if _matches(x, cat, status, qty_only, search, mapping)], base_qty
    )
    side = bool(ss.why_sku or ss.pending)
    table, panel = st.columns([2.8, 1.2]) if side else (st.container(), None)
    with table:
        df = order_frame(shown, base_qty, mapping)
        key = f"grid_{cat}_{status}_{qty_only}_{search}_{ss.nonce}"
        edited = st.data_editor(style_order_frame(df), key=key, hide_index=True, width="stretch", height=TABLE_HEIGHT, placeholder="—",
                                column_config=ORDER_COLUMN_CONFIG,
                                disabled=[c for c in df.columns if c not in EDITABLE_COLUMNS])  # fmt: skip
        seen = len([x for x in res.lines if x.sku in ss.seen])
        st.caption(f"{len(shown)} ligne(s) affichée(s) sur {len(res.lines)} · lignes validées : "
                   f"{seen} / {len(res.lines)} · clic sur un en-tête pour trier · case Détail pour le « Pourquoi »")  # fmt: skip
    if _process_edits(edited, {x.sku: x for x in shown}):
        ss.nonce += 1
        st.rerun()
    if panel is not None:
        with panel, st.container(border=True, height=TABLE_HEIGHT + 38, key="why_panel"):
            if ss.pending:
                _justify_block(res)
            if ss.why_sku:
                if ss.pending:
                    st.divider()
                _why_panel(res, ss.why_sku, mapping)
    if not ss.pending:
        st.button("Continuer vers l'export", on_click=go, args=(EXPORT,), key="to_export")
