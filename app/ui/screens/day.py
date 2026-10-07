"""Écran 3 — Journée : plusieurs boutiques, cover par boutique, un calcul, un zip des LT."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.domain.models import PlanningParameters
from app.engines.export import lt_filename
from app.engines.schedule import boutiques_ordering_on
from app.exports.files import lt_zip
from app.ingestion.masters import all_boutiques, build_inputs_for_boutique
from app.services.planning import DayOutcome, run_day
from app.storage import db
from app.ui.common import (
    DEPOT,
    MODES,
    author,
    boutique_label,
    day_data,
    fmt_1,
    fmt_int,
    go,
    run_date,
    ss,
    workspace,
)
from app.ui.screens.order import FALLBACK_WEEKS_DEFAULT, open_result


def _save_day(outcomes: list[DayOutcome], when: datetime) -> None:
    data = day_data()
    conn = db.connect(workspace().db_path)
    try:
        for o in outcomes:
            if o.result is None:
                continue
            run_id = db.save_run(conn, o.result, author=author(), sources=data.source_hashes)
            if any(x.qty > 0 for x in o.result.lines):
                db.mark_exported(conn, run_id, lt_filename(o.boutique, when))
    finally:
        conn.close()


def _confirm_all() -> None:
    ss.day_confirm_all = True
    ss.nonce += 1


def render() -> None:
    st.header("Journée")
    data = day_data()
    if data is None:
        st.info("Dépose d'abord les exports du jour.")
        st.button("Aller au dépôt du jour", on_click=go, args=(DEPOT,))
        return
    names = data.masters.boutique_names
    boutiques = all_boutiques(data.masters, data.stock)
    planned = {d.boutique: d for d in boutiques_ordering_on(data.masters.schedule, run_date())}

    c1, c2, c3 = st.columns([5, 1, 1.2])
    chosen = c1.multiselect("Boutiques", boutiques, default=[b for b in planned if b in boutiques],
                            format_func=lambda b: boutique_label(b, names), key=f"day_btq_{run_date()}",
                            placeholder="Ajouter une boutique")  # fmt: skip
    history = c2.number_input("Historique (j)", min_value=1, max_value=56, value=7, key="day_hist")
    mode = c3.selectbox("Mode", list(MODES), key="day_mode")
    st.caption(f"Présélection du Schedule pour le {run_date():%d/%m/%Y} : "
               f"{len(planned)} boutique(s), modifiable.")  # fmt: skip

    confirm_all = ss.pop("day_confirm_all", False)
    table = pd.DataFrame(
        [
            {
                "Boutique": b,
                "Nom": names.get(b, ""),
                "Livraison": planned[b].delivery_date if b in planned else None,
                "Date confirmée": confirm_all and b in planned,
                "Cover (j)": None,
                "Cover café (j)": None,
            }
            for b in chosen
        ],  # fmt: skip
        columns=["Boutique", "Nom", "Livraison", "Date confirmée", "Cover (j)", "Cover café (j)"],
    )
    # Colonnes numériques vides : NaN (affiché vide) plutôt que None (affiché « None »).
    table[["Cover (j)", "Cover café (j)"]] = table[["Cover (j)", "Cover café (j)"]].astype(float)
    table["Livraison"] = pd.to_datetime(table["Livraison"])
    edited = st.data_editor(
        table, key=f"day_editor_{'-'.join(chosen)}_{ss.nonce}", hide_index=True, width="stretch",
        disabled=["Boutique", "Nom"],
        column_config={
            "Boutique": st.column_config.TextColumn(width=80),
            "Nom": st.column_config.TextColumn(width=200),
            "Livraison": st.column_config.DateColumn(format="DD/MM/YYYY", min_value=run_date(), width=120),
            "Date confirmée": st.column_config.CheckboxColumn(width=110),
            "Cover (j)": st.column_config.NumberColumn(min_value=0.5, max_value=60, step=0.5, width=90),
            "Cover café (j)": st.column_config.NumberColumn(min_value=0.5, max_value=60, step=0.5, width=110),
        },
    )  # fmt: skip
    b1, b2, _ = st.columns([1.6, 1.6, 4])
    b1.button("Confirmer les dates suggérées", on_click=_confirm_all, key="day_confirm",
              disabled=not chosen)  # fmt: skip
    if b2.button("Calculer toutes les commandes", type="primary", key="day_compute"):
        errors, requests = [], []
        for row in edited.to_dict("records"):
            b = row["Boutique"]
            if row["Livraison"] is None or pd.isna(row["Livraison"]) or not row["Date confirmée"]:
                errors.append(f"{b} : date de livraison à saisir ou confirmer")
                continue
            if row["Cover (j)"] is None or pd.isna(row["Cover (j)"]):
                errors.append(f"{b} : cover manquant")
                continue
            coffee = row["Cover café (j)"]
            try:
                requests.append(PlanningParameters(
                    boutique=b, run_date=run_date(), delivery_date=pd.Timestamp(row["Livraison"]).date(),
                    cover_days=float(row["Cover (j)"]),
                    coffee_cover_days=None if coffee is None or pd.isna(coffee) else float(coffee),
                ))  # fmt: skip
            except ValidationError as exc:
                errors.append(f"{b} : " + "; ".join(e["msg"] for e in exc.errors()))
        if not chosen:
            st.error("Choisis au moins une boutique.")
        elif errors:
            st.error(" · ".join(errors))
        else:

            def build(boutique: str):  # noqa: ANN202
                return build_inputs_for_boutique(
                    boutique, data.stock, data.movements, data.masters.products,
                    data.masters.conversions, data.masters.dc_mapping, sources=data.source_hashes,
                    history_days=int(history), fallback_weeks=FALLBACK_WEEKS_DEFAULT,
                    portfolio=data.masters.portfolio.get(boutique, {}), exclusions=data.exclusions,
                )  # fmt: skip

            ss.day_outcomes = run_day(requests, MODES[mode], build_inputs=build,
                                      business_inputs=data.business)  # fmt: skip

    outcomes: list[DayOutcome] | None = ss.day_outcomes
    if not outcomes:
        return
    st.divider()
    head = st.columns([2.6, 1.1, 0.8, 1, 0.9, 0.8, 0.8, 2.2, 1])
    for col, title in zip(head, ["Boutique", "Livraison", "Lignes", "Unités", "Palettes", "REVIEW",
                                 "BLOCKED", "Alertes", ""], strict=True):  # fmt: skip
        col.markdown(f"**{title}**")
    for i, o in enumerate(outcomes):
        cols = st.columns([2.6, 1.1, 0.8, 1, 0.9, 0.8, 0.8, 2.2, 1], vertical_alignment="center")
        cols[0].write(boutique_label(o.boutique, names))
        if o.result is None:
            cols[7].error(o.error or "Erreur")
            continue
        r = o.result
        cols[1].write(f"{r.params.delivery_date:%d/%m}")
        cols[2].write(fmt_int(r.summary.lines_ordered))
        cols[3].write(fmt_int(r.summary.units_total))
        cols[4].write(fmt_1(r.pallets.total_pallets))
        cols[5].write(f"🟠 {r.summary.by_status['REVIEW']}")
        cols[6].write(f"🔴 {r.summary.by_status['BLOCKED']}")
        cols[7].write(" | ".join(r.rule_warnings) or "—")
        cols[8].button("Ouvrir", key=f"open_{i}_{o.boutique}", on_click=open_result, args=(r,))
    ok = [o for o in outcomes if o.result is not None]
    if ok:
        when = datetime.now()
        name, payload = lt_zip([o.result for o in ok], when)
        label = (
            "Télécharger le fichier LT (zip)"
            if len(ok) == 1
            else f"Télécharger les {len(ok)} fichiers LT (zip)"
        )
        st.download_button(label, data=payload, file_name=name, mime="application/zip", type="primary",
                           key="day_zip", disabled=not author(), on_click=_save_day, args=(ok, when))  # fmt: skip
        if not author():
            st.caption("Indique ton nom dans la barre de gauche pour télécharger.")
