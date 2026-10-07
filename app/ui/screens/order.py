"""Écran 2 — Commande boutique : paramètres, tuiles, tableau par famille, « Pourquoi »."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.domain.models import (
    OverrideCategory,
    PlanningParameters,
    RecommendationLine,
    Status,
)
from app.engines.families import DISPLAY_ORDER
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
    ORDER_COLUMN_CONFIG,
    SOURCE_LABEL,
    STATUS_ICON,
    author,
    boutique_label,
    current,
    day_data,
    exceptions_first,
    explain_steps,
    family_of,
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
)

CHIPS = ("Tous", "OK", "REVIEW", "BLOCKED", "Qty > 0", "Promo/Allocation", "Lancement",
         "Conversion", "Retour DC")  # fmt: skip
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


def _matches(chip: str, x: RecommendationLine) -> bool:
    e = x.explanation
    if chip in ("OK", "REVIEW", "BLOCKED"):
        return x.status.value == chip
    if chip == "Qty > 0":
        return x.qty > 0
    if chip == "Promo/Allocation":
        ov = ss.overrides.get(x.sku)
        return x.source.value == "allocation" or (
            ov is not None and ov.category is OverrideCategory.PROMO
        )
    if chip == "Lancement":
        return x.source.value == "launch"
    if chip == "Conversion":
        return bool(e["inputs"]["sales_from_old_skus"]) or "old_sku_blocked" in e["rules_triggered"] \
            or any("onversion" in r for r in x.reasons)  # fmt: skip
    if chip == "Retour DC":
        return "dc_return" in e["rules_triggered"] or "dc_out_of_stock" in e["rules_triggered"]
    return True


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
    with cols[6].popover("⚙️", help="Options avancées", width="stretch"):
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
    cols[2].metric("🟢 OK", fmt_int(s.by_status["OK"]))
    cols[3].metric("🟠 REVIEW", fmt_int(s.by_status["REVIEW"]))
    cols[4].metric("🔴 BLOCKED", fmt_int(s.by_status["BLOCKED"]))


def _process_edits(edited: pd.DataFrame, lines: dict[str, RecommendationLine]) -> bool:
    """Met à jour Vu / modifications en attente ; True si le panneau Pourquoi est demandé."""
    for row in edited.to_dict("records"):
        sku = row["SKU"]
        if row["🔍"]:
            ss.why_sku = sku
            return True
        if bool(row["Vu"]) != (sku in ss.seen):
            (ss.seen.add if row["Vu"] else ss.seen.discard)(sku)
        qty = row["Qty finale"]
        if pd.isna(qty):
            continue
        if int(qty) != lines[sku].qty:
            ss.pending[sku] = int(qty)
        else:
            ss.pending.pop(sku, None)
    return False


def _editor(lines: list[RecommendationLine], key: str, base_qty: dict[str, int]) -> bool:
    df = order_frame(lines, base_qty)
    height = min(38 + 35 * max(len(df), 1), 460)
    edited = st.data_editor(df, key=key, hide_index=True, width="stretch", height=height,
                            column_config=ORDER_COLUMN_CONFIG,
                            disabled=[c for c in df.columns if c not in EDITABLE_COLUMNS])  # fmt: skip
    return _process_edits(edited, {x.sku: x for x in lines})


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
    st.subheader(f"Modifications à justifier ({len(ss.pending)})")
    cats = list(CATEGORY_BY_LABEL)
    for sku, qty in ss.pending.items():
        c = st.columns([3, 2, 4])
        c[0].markdown(
            f"**{sku}** {lines[sku].description or ''}  \n{fmt_int(lines[sku].qty)} → **{fmt_int(qty)}**"
        )
        c[1].selectbox("Catégorie", cats, index=None, key=f"cat_{sku}", placeholder="Catégorie",
                       label_visibility="collapsed")  # fmt: skip
        c[2].text_input(
            "Raison", key=f"reason_{sku}", placeholder="Raison", label_visibility="collapsed"
        )
    b1, b2, _ = st.columns([1.3, 1, 4])
    if b1.button("Valider les modifications", type="primary", key="validate_pending"):
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
    if b2.button("Annuler", key="cancel_pending"):
        ss.pending = {}
        ss.nonce += 1
        st.rerun()


@st.dialog("Pourquoi cette quantité ?", width="large")
def why_dialog(sku: str) -> None:
    res = current()
    line = next(x for x in res.lines if x.sku == sku)
    base_qty = {x.sku: x.qty for x in res.base_lines}
    e, inp = line.explanation, line.explanation["inputs"]
    st.markdown(f"### {sku} — {line.description or 'sans description'}")
    st.markdown(f"{STATUS_ICON[line.status.value]} · {family_of(line)} · source : "
                f"**{SOURCE_LABEL[line.source.value]}** · multiple {fmt_int(e['multiple'])}")  # fmt: skip
    for i, step in enumerate(explain_steps(line), start=1):
        st.markdown(f"{i}. {step}")
    dc = inp["dc_available"]
    st.markdown(f"Stock DC : **{fmt_int(dc) if dc is not None else 'absent'}** · "
                f"stock projeté le jour de la livraison : **{fmt_int(e['projected_stock_at_delivery'])}**")  # fmt: skip
    if line.reasons:
        st.markdown("**Raisons et alertes**")
        for r in line.reasons:
            st.markdown(f"- {r}")
    st.divider()
    st.markdown("**Modifier la quantité**")
    c = st.columns([1, 1.4, 2.4])
    qty = c[0].number_input("Qty finale", min_value=0, step=int(e["multiple"] or 1), value=int(line.qty),
                            key="dlg_qty")  # fmt: skip
    cat = c[1].selectbox(
        "Catégorie", list(CATEGORY_BY_LABEL), index=None, key="dlg_cat", placeholder="Choisir"
    )
    reason = c[2].text_input("Raison", key="dlg_reason")
    if st.button("Enregistrer", type="primary", key="dlg_save"):
        if not author():
            st.error("Indique ton nom dans la barre de gauche.")
        elif qty == base_qty[sku]:
            new = dict(ss.overrides)
            new.pop(sku, None)
            _apply_overrides(new)
        elif not cat or len(reason.strip()) < 3:
            st.error("Catégorie et raison obligatoires.")
        else:
            new = dict(ss.overrides)
            new[sku] = make_override(sku, base_qty[sku], int(qty), cat, reason.strip())
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
    names = data.masters.boutique_names
    st.markdown(
        f"**{boutique_label(p.boutique, names)}** · DC {res.dc} · livraison **{fmt_date(p.delivery_date)}** · "
        f"cover {fmt_1(p.cover_days)} j"
        + (f" (café {fmt_1(p.coffee_cover_days)} j)" if p.coffee_cover_days else "")
        + f" · historique {res.history_days} j · {MODE_LABEL[res.mode.value]}"
    )
    _tiles(res)
    for w in res.rule_warnings:
        st.warning(w)
    seen = len([x for x in res.lines if x.sku in ss.seen])
    st.progress(seen / max(len(res.lines), 1), text=f"Lignes vérifiées : {seen} / {len(res.lines)}")

    chip = (
        st.pills("Filtre", CHIPS, default="Tous", key="chip", label_visibility="collapsed")
        or "Tous"
    )
    t2, t3, _ = st.columns([2.2, 2, 3], vertical_alignment="bottom")
    search = t2.text_input("Recherche", placeholder="Rechercher un SKU ou une description", key="search",
                           label_visibility="collapsed").strip().lower()  # fmt: skip
    if t3.button("✓ Accepter toutes les lignes OK", key="accept_ok", width="stretch"):
        ss.seen |= {x.sku for x in res.lines if x.status is Status.OK}
        ss.nonce += 1
        st.rerun()

    base_qty = {x.sku: x.qty for x in res.base_lines}
    shown = [x for x in res.lines if _matches(chip, x) and (not search or search in x.sku.lower()
             or search in (x.description or "").lower())]  # fmt: skip
    wants_why = False
    for fam in DISPLAY_ORDER:
        fam_lines = exceptions_first([x for x in shown if family_of(x) == fam])
        if not fam_lines:
            continue
        units = sum(x.qty for x in fam_lines)
        flagged = sum(1 for x in fam_lines if x.status is not Status.OK)
        st.markdown(f"#### {fam} · {len(fam_lines)} lignes · {fmt_int(units)} unités"
                    + (f" · {flagged} à vérifier" if flagged else ""))  # fmt: skip
        main = [x for x in fam_lines if x.status is not Status.OK or x.qty > 0
                or x.sku in ss.overrides or x.sku in ss.pending]  # fmt: skip
        rest = [x for x in fam_lines if x not in main]
        key = f"ed_{fam}_{chip}_{search}_{ss.nonce}"
        if main:
            wants_why |= _editor(main, key + "_main", base_qty)
        if rest:
            with st.expander(f"Lignes à 0 sans alerte ({len(rest)})"):
                wants_why |= _editor(rest, key + "_zero", base_qty)
    if wants_why:
        ss.nonce += 1
        st.rerun()
    if not shown:
        st.info("Aucune ligne pour ce filtre.")

    if ss.pending:
        _justify_block(res)
    else:
        st.button("Continuer vers l'export", on_click=go, args=(EXPORT,), key="to_export")

    if ss.why_sku:
        sku, ss.why_sku = ss.why_sku, None
        why_dialog(sku)
