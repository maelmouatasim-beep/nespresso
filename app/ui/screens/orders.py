"""Écran 2 — Commandes : une boutique = une commande simple, plusieurs = une journée.

Le planner ajoute les boutiques une par une (rien n'est présélectionné), saisit pour chacune
la date de livraison et les cover, calcule, puis ouvre l'onglet d'une boutique pour voir sa
table de commande. Le fichier LT se télécharge pour la boutique affichée ou pour toutes
(zip), chaque fois avec le garde-fou : lignes REVIEW et BLOCKED confirmées (D-039) et les
3 points des événements SKU.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.domain.models import PlanningParameters, Status
from app.engines.events import event_tags
from app.engines.export import lt_content, lt_filename
from app.exports.files import lt_zip
from app.ingestion.masters import all_boutiques, build_inputs_for_boutique
from app.services.planning import PlanningResult, run_planning, with_overrides
from app.storage import db
from app.ui.common import (
    DEPOT,
    EVENTS,
    EXPORT,
    MODES,
    ORDER_COLUMN_CONFIG,
    activate,
    author,
    boutique_label,
    current,
    day_data,
    default_order,
    event_context,
    go,
    order_frame,
    order_state,
    reset_order,
    run_date,
    short_reasons,
    ss,
    style_order_frame,
    workspace,
)
from app.ui.gate import close_requests_prompt, requests_after_export
from app.ui.screens.export import save_run
from app.ui.screens.order import FALLBACK_WEEKS_DEFAULT, render_order, set_confirmed

PARAM_FIELDS = ("delivery", "cover", "coffee", "history", "mode", "weeks", "codes")
TO_COMPUTE, COMPUTED, CONFIRMED = "à calculer", "calculé", "confirmé"
STATUS_DOT = {TO_COMPUTE: "○", COMPUTED: "◐", CONFIRMED: "●"}
PARAM_WIDTHS = [2.1, 1.25, 1, 1, 0.85, 1.05, 0.45, 0.9]


# --- État par boutique ------------------------------------------------------------------


def _empty_params() -> dict:
    return {"delivery": None, "cover": None, "coffee": None, "history": 7, "mode": "Standard",
            "weeks": FALLBACK_WEEKS_DEFAULT, "codes": None}  # fmt: skip


def pending_lines(base_lines, confirmed: set[str]) -> list:  # noqa: ANN001
    """Lignes qui ne partiront pas sans confirmation : REVIEW, BLOCKED, ou flaguées par un
    événement (lancement, demande boutique, promo…), tant qu'elles ne sont pas confirmées."""
    return [b for b in base_lines
            if b.sku not in confirmed and (b.status is not Status.OK or event_tags(b))]  # fmt: skip


def add_boutique(boutique: str) -> None:
    """Ajoute un onglet. Les paramètres de la première boutique sont proposés (modifiables)."""
    if not boutique or boutique in ss.orders:
        return
    first = next(iter(ss.orders.values()), None)
    params = dict(first["params"], codes=None) if first else _empty_params()
    ss.orders[boutique] = {"params": params, "state": None, "computed": None, "error": None}
    if ss.active is None:
        activate(boutique)


def remove_boutique(boutique: str) -> None:
    if boutique not in ss.orders:
        return
    if ss.active == boutique:
        reset_order()
        ss.active = None
    del ss.orders[boutique]
    for k in [
        k for k in ss if isinstance(k, str) and k.endswith(f"_{boutique}") and k.startswith("p_")
    ]:
        del ss[k]
    if ss.active is None and ss.orders:
        activate(next(iter(ss.orders)))


def _on_add() -> None:
    add_boutique(ss.get("add_boutique") or "")
    ss.add_boutique = None


@dataclass(frozen=True)
class Readiness:
    """Où en est une boutique : résultat (avec modifications) et ce qui bloque le fichier LT."""

    boutique: str
    status: str  # à calculer / calculé / confirmé
    result: PlanningResult | None
    review: int = 0  # lignes REVIEW non confirmées
    blocked: int = 0  # lignes BLOCKED non confirmées
    to_treat: int = 0  # OK + REVIEW non confirmés (filtre « À traiter »)
    pending: int = 0  # lignes qui ne partiront pas sans confirmation
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.result is not None and not self.reason


def readiness(boutique: str) -> Readiness:
    slot, state = ss.orders[boutique], order_state(boutique)
    if state is None or state["base"] is None:
        return Readiness(boutique, TO_COMPUTE, None, reason="à calculer")
    if slot["computed"] != slot["params"]:
        return Readiness(boutique, TO_COMPUTE, None, reason="paramètres modifiés, à recalculer")
    data = day_data()
    assert data is not None
    base, overrides = state["base"], state["overrides"]
    result = with_overrides(base, overrides.values(), data.masters.products)
    confirmed = state["seen"] | state["confirmed_blocks"]
    open_ = [b for b in base.base_lines if b.sku not in confirmed]
    review = sum(b.status is Status.REVIEW for b in open_)
    blocked = sum(b.status is Status.BLOCKED for b in open_)
    to_treat = sum(b.status is not Status.BLOCKED for b in open_)
    pending = len(pending_lines(base.base_lines, confirmed))
    reason = f"{pending} ligne(s) à confirmer" if pending else ""
    status = COMPUTED if review or blocked else CONFIRMED
    return Readiness(boutique, status, result, review, blocked, to_treat, pending, reason)


# --- Calcul -----------------------------------------------------------------------------


def _missing(params: dict) -> list[str]:
    return [n for n, k in (("la date de livraison", "delivery"), ("le cover non-café", "cover"))
            if not params[k]]  # fmt: skip


def _sync(boutique: str) -> None:
    """Reprend les valeurs des champs : un clic sur « Calculer » juste après une saisie arrive
    avant que la ligne de paramètres soit redessinée."""
    p = ss.orders[boutique]["params"]
    for field, key in (("delivery", "p_delivery"), ("cover", "p_cover"), ("coffee", "p_coffee"),
                       ("history", "p_hist"), ("mode", "p_mode"), ("weeks", "p_fallback")):  # fmt: skip
        if f"{key}_{boutique}" in ss:
            p[field] = ss[f"{key}_{boutique}"]


def compute(boutique: str) -> bool:
    """Calcule la commande d'une boutique avec ses paramètres. False = erreur (gardée)."""
    data = day_data()
    assert data is not None
    _sync(boutique)
    slot = ss.orders[boutique]
    p, slot["error"] = slot["params"], None
    missing = _missing(p)
    if missing:
        slot["error"] = "Il manque " + ", ".join(missing) + "."
        return False
    try:
        params = PlanningParameters(boutique=boutique, run_date=run_date(), delivery_date=p["delivery"],
                                    cover_days=p["cover"], coffee_cover_days=p["coffee"])  # fmt: skip
        inputs, dc = build_inputs_for_boutique(
            boutique, data.stock, data.movements, data.masters.products, data.masters.conversions,
            data.masters.dc_mapping, included_movement_codes=p["codes"], sources=data.source_hashes,
            history_days=int(p["history"]), fallback_weeks=int(p["weeks"]),
            portfolio=data.masters.portfolio.get(boutique, {}), exclusions=data.exclusions,
        )  # fmt: skip
        result = run_planning(inputs, params, MODES[p["mode"]], dc=dc, business_inputs=data.business,
                              event_context=event_context())  # fmt: skip
    except ValidationError as exc:
        slot["error"] = "; ".join(e["msg"] for e in exc.errors())
        return False
    except ValueError as exc:
        slot["error"] = f"Calcul impossible : {exc}"
        return False
    fresh = {"base": result, "overrides": {}, "seen": set(), "confirmed_blocks": set(),
             "include_ok": False, "saved_run_id": None, "why_sku": None}  # fmt: skip
    slot["computed"] = dict(p)
    if boutique == ss.active:
        reset_order()
        ss.base = result
    else:
        slot["state"] = fresh
    return True


def _compute_one(boutique: str) -> None:
    if compute(boutique):
        activate(boutique)


def _compute_all() -> None:
    for b in list(ss.orders):
        compute(b)
    if ss.active is None and ss.orders:
        activate(next(iter(ss.orders)))


# --- Paramètres -------------------------------------------------------------------------


def _params_rows(data) -> None:  # noqa: ANN001
    names = data.masters.boutique_names
    head = st.columns(PARAM_WIDTHS)
    titles = ["Boutique", "Livraison", "Cover non-café (j)", "Cover café (j)", "Historique (j)",
              "Mode", "", ""]  # fmt: skip
    for col, title in zip(head, titles, strict=True):
        col.markdown(f"**{title}**" if title else "")
    for b, slot in ss.orders.items():
        p = slot["params"]
        cols = st.columns(PARAM_WIDTHS, vertical_alignment="center")
        cols[0].markdown(boutique_label(b, names))
        # Les valeurs sont gardées dans ss.orders : elles survivent à un changement d'écran.
        p["delivery"] = cols[1].date_input("Livraison", value=p["delivery"], min_value=run_date(),
                                           key=f"p_delivery_{b}", format="DD/MM/YYYY",
                                           label_visibility="collapsed")  # fmt: skip
        p["cover"] = cols[2].number_input("Cover non-café (j)", min_value=0.5, max_value=60.0, step=0.5,
                                          value=p["cover"], key=f"p_cover_{b}", format="%.1f",
                                          label_visibility="collapsed")  # fmt: skip
        p["coffee"] = cols[3].number_input("Cover café (j)", min_value=0.5, max_value=60.0, step=0.5,
                                           value=p["coffee"], key=f"p_coffee_{b}", format="%.1f",
                                           placeholder="facultatif", label_visibility="collapsed",
                                           help="Facultatif : cover propre aux cafés (type C)")  # fmt: skip
        p["history"] = int(cols[4].number_input("Historique (j)", min_value=1, max_value=56, step=1,
                                                value=int(p["history"]), key=f"p_hist_{b}",
                                                label_visibility="collapsed"))  # fmt: skip
        p["mode"] = cols[5].selectbox("Mode", list(MODES), index=list(MODES).index(p["mode"]),
                                      key=f"p_mode_{b}", label_visibility="collapsed")  # fmt: skip
        with cols[6].popover(":material/tune:", help="Options avancées", width="stretch"):
            p["weeks"] = int(st.number_input("Semaines de secours (SKU dormants)", min_value=1, max_value=12,
                                             value=int(p["weeks"]), step=1, key=f"p_fallback_{b}"))  # fmt: skip
            all_codes = sorted({m.movement_code for m in data.movements if m.location == b})
            codes = st.multiselect("Codes de mouvement comptés", all_codes, default=p["codes"] or all_codes,
                                   placeholder="Aucun", key=f"p_codes_{b}")  # fmt: skip
            p["codes"] = None if set(codes) == set(all_codes) else codes
        cols[7].button("Calculer", key=f"compute_{b}", on_click=_compute_one, args=(b,),
                       type="primary" if slot["computed"] != p else "secondary", width="stretch")  # fmt: skip
        if slot["error"]:
            st.error(f"{b} : {slot['error']}")
        elif slot["computed"] is not None and slot["computed"] != p:
            st.caption(f":orange[:material/warning:] {b} : paramètres modifiés depuis le calcul, "
                       "recalcule pour les appliquer.")  # fmt: skip


# --- Onglets et récapitulatif -----------------------------------------------------------


def _tabs(states: dict[str, Readiness]) -> None:
    """Une puce par boutique (clic = afficher sa commande), avec une croix pour la retirer."""
    with st.container(horizontal=True, key="order_tabs"):
        for b, r in states.items():
            label = f"{STATUS_DOT[r.status]} {b} · {r.status}"
            if r.result is not None:
                label += f" · REVIEW {r.review} · BLOCKED {r.blocked}"
            with st.container(horizontal=True, gap=None, width="content", key=f"tab_{b}"):
                st.button(label, key=f"tab_open_{b}", on_click=activate, args=(b,),
                          type="primary" if b == ss.active else "secondary")  # fmt: skip
                st.button("", icon=":material/close:", key=f"tab_close_{b}", help=f"Retirer {b}",
                          on_click=remove_boutique, args=(b,), type="tertiary")  # fmt: skip


def _recap(states: dict[str, Readiness]) -> None:
    """Unités, palettes et lignes à traiter, par boutique et au total."""
    rows = []
    for b, r in states.items():
        res = r.result
        rows.append({
            "Boutique": b, "Statut": r.status,
            "Livraison": res.params.delivery_date if res else None,
            "Unités": res.summary.units_total if res else None,
            "Palettes": round(res.pallets.total_pallets, 1) if res else None,
            "À traiter": r.to_treat if res else None,
            "REVIEW": r.review if res else None, "BLOCKED": r.blocked if res else None,
        })  # fmt: skip
    done = [r for r in states.values() if r.result is not None]
    rows.append({
        "Boutique": "Total", "Statut": f"{len(done)} / {len(states)} calculée(s)", "Livraison": None,
        "Unités": sum(r.result.summary.units_total for r in done if r.result),
        "Palettes": round(sum(r.result.pallets.total_pallets for r in done if r.result), 1),
        "À traiter": sum(r.to_treat for r in done), "REVIEW": sum(r.review for r in done),
        "BLOCKED": sum(r.blocked for r in done),
    })  # fmt: skip
    df = pd.DataFrame(rows)
    df["Livraison"] = pd.to_datetime(df["Livraison"])
    st.dataframe(df, hide_index=True, width="stretch", key="order_recap", column_config={
        "Livraison": st.column_config.DateColumn(format="DD/MM/YYYY"),
        "Unités": st.column_config.NumberColumn(format="localized"),
        "Palettes": st.column_config.NumberColumn(format="%.1f"),
        "À traiter": st.column_config.NumberColumn(help="Lignes OK et REVIEW pas encore confirmées"),
    })  # fmt: skip


# --- Export -----------------------------------------------------------------------------


PENDING_COLUMNS = ["Confirmé", "Statut", "SKU", "Description", "Qty proposée", "Raisons"]


def _short_reason(line, base) -> str:  # noqa: ANN001
    """Raison en quelques mots : les raisons du calcul, sinon le titre de l'événement."""
    if line.reasons:
        return short_reasons(line, base)
    return ", ".join(dict.fromkeys(t["title"] for t in event_tags(base)))


def _pending_list(res, mapping: dict[str, str]) -> None:  # noqa: ANN001
    """« X lignes ne partiront pas si tu ne les confirmes pas » : même présentation que la table,
    filtrée, une case Confirmé par ligne et « Tout confirmer ». Vide → « Tout est confirmé »."""
    base = {x.sku: x for x in res.base_lines}
    pending = pending_lines(res.base_lines, ss.seen | ss.confirmed_blocks)
    if not pending:
        st.markdown("#### :green[:material/check_circle:] Tout est confirmé")
        return
    by_sku = {x.sku: x for x in res.lines}
    shown = default_order([by_sku[b.sku] for b in pending], base)
    c1, c2 = st.columns([4, 1.2], vertical_alignment="center")
    c1.markdown(f"#### {len(pending)} ligne(s) ne partiront pas si tu ne les confirmes pas")
    if c2.button(
        "Tout confirmer", key="confirm_pending", icon=":material/done_all:", width="stretch"
    ):
        set_confirmed([x.sku for x in shown], True)
        st.rerun()
    df = order_frame(shown, base, mapping)[PENDING_COLUMNS].reset_index(drop=True)
    df["Raisons"] = [_short_reason(x, base[x.sku]) for x in shown]
    edited = st.data_editor(style_order_frame(df), key=f"pending_{res.params.boutique}_{ss.nonce}",
                            hide_index=True, width="stretch", height=min(400, 40 + 35 * len(df)),
                            column_config={k: ORDER_COLUMN_CONFIG[k] for k in PENDING_COLUMNS},
                            disabled=[c for c in PENDING_COLUMNS if c != "Confirmé"])  # fmt: skip
    confirm = [r["SKU"] for r in edited.to_dict("records") if bool(r["Confirmé"])]
    if confirm:
        set_confirmed(confirm, True)
        st.rerun()


def _on_lt(result, filename: str) -> None:  # noqa: ANN001
    requests_after_export(list(result.events))
    run_id = save_run(result)
    if run_id:
        conn = db.connect(workspace().db_path)
        try:
            db.mark_exported(conn, run_id, filename)
        finally:
            conn.close()


def _on_zip(boutiques: list[str], when: datetime) -> None:
    """Chaque boutique du zip est enregistrée dans l'historique, avec ses modifications."""
    data = day_data()
    assert data is not None
    conn = db.connect(workspace().db_path)
    try:
        for b in boutiques:
            state = order_state(b)
            assert state is not None
            result = with_overrides(
                state["base"], state["overrides"].values(), data.masters.products
            )
            requests_after_export(list(result.events))
            run_id = db.save_run(conn, result, author=author(), sources=data.source_hashes,
                                 overrides=state["overrides"].values())  # fmt: skip
            if any(x.qty > 0 for x in result.lines):
                db.mark_exported(conn, run_id, lt_filename(b, when))
    finally:
        conn.close()


def _export(res, states: dict[str, Readiness], mapping: dict[str, str]) -> None:  # noqa: ANN001
    _pending_list(res, mapping)
    if not author():
        st.error("Choisis ton prénom (Planner, barre de gauche) pour télécharger.")
    now = datetime.now()
    mine = states[res.params.boutique]
    name = lt_filename(res.params.boutique, now)
    ready = [b for b, r in states.items() if r.ready]
    waiting = {b: r.reason for b, r in states.items() if not r.ready}
    c1, c2, c3 = st.columns([1.6, 1.8, 1.3])
    c1.download_button(f"Télécharger le LT de {res.params.boutique}", data=lt_content(res.lines),
                       file_name=name, mime="text/csv", type="primary", key="dl_lt",
                       disabled=not author() or mine.pending > 0,
                       on_click=_on_lt, args=(res, name), width="stretch")  # fmt: skip
    zip_name, payload = lt_zip([r.result for r in states.values() if r.ready and r.result], now)
    c2.download_button(f"Télécharger tous les LT (zip, {len(states)} boutique(s))", data=payload,
                       file_name=zip_name, mime="application/zip", key="dl_zip",
                       disabled=not author() or bool(waiting) or not ready, on_click=_on_zip,
                       args=(ready, now), width="stretch")  # fmt: skip
    c3.button("Récap et Excel détaillé", on_click=go, args=(EXPORT,), key="to_export",
              icon=":material/description:", width="stretch")  # fmt: skip
    if waiting:
        listed = " · ".join(f"**{b}** : {why}" for b, why in waiting.items())
        st.caption(
            f":material/lock: Le zip attend que chaque boutique soit prête. Encore bloquée(s) : {listed}"
        )
    close_requests_prompt()
    st.caption("Le fichier LT s'importe à la main dans Nessoft : l'outil n'y écrit jamais.")


# --- Page -------------------------------------------------------------------------------


def render() -> None:
    st.header("Commandes")
    data = day_data()
    if data is None:
        st.info("Dépose d'abord les exports du jour.")
        st.button("Aller au dépôt du jour", on_click=go, args=(DEPOT,))
        return
    names = data.masters.boutique_names
    choices = [b for b in all_boutiques(data.masters, data.stock) if b not in ss.orders]
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    c1.selectbox("Ajouter une boutique", choices, index=None, key="add_boutique", on_change=_on_add,
                 format_func=lambda b: boutique_label(b, names),
                 placeholder="Rechercher par code ou nom")  # fmt: skip
    c2.button("Tout calculer", key="compute_all", on_click=_compute_all, disabled=not ss.orders,
              icon=":material/calculate:", width="stretch")  # fmt: skip
    if not ss.orders:
        st.caption("Ajoute une boutique pour une commande simple, plusieurs pour une journée. "
                   "Rien n'est présélectionné.")  # fmt: skip
        return
    _params_rows(data)
    states = {b: readiness(b) for b in ss.orders}
    _tabs(states)
    _recap(states)
    res = current()
    if res is None:
        if ss.active:
            st.info(f"{ss.active} : saisis la date de livraison et le cover, puis « Calculer ».")
        return
    st.divider()
    render_order(res, data)
    _export(res, states, data.categories)
    st.button(
        "Événements SKU", on_click=go, args=(EVENTS,), key="to_events", icon=":material/event:"
    )
