"""Écran 1 — Dépôt du jour : les deux exports, leur fraîcheur, les contrôles qualité."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from app.ingestion.depot import freshness
from app.ui.common import (
    FRESH_ICON,
    ORDER,
    REFS,
    day_data,
    fmt_date,
    fmt_int,
    go,
    reset_all,
    run_date,
    ss,
    workspace,
)
from app.ui.workspace import DEPOT_LABELS, MOVES, STOCK, detect


def _deposit(kind: str, upload) -> None:
    data = upload.getvalue()
    extraction = detect(upload.name, data)
    workspace().save_depot(kind, upload.name, data, extraction, date.today())
    reset_all()


def _file_card(kind: str, info: dict | None) -> None:
    label = DEPOT_LABELS[kind]
    st.markdown(f"#### {label}")
    up = st.file_uploader(f"Déposer l'export {label}", type=["csv", "xlsx"], key=f"up_{kind}_{ss.nonce}",
                          label_visibility="collapsed")  # fmt: skip
    if up is not None:
        _deposit(kind, up)
        st.rerun()
    if info is None:
        st.info("Aucun fichier déposé.")
        return
    day = date.fromisoformat(info["extraction"]) if info.get("extraction") else None
    fresh = freshness(day, run_date())
    st.markdown(
        f"{FRESH_ICON[fresh.level]} **{fresh.label}**  \n"
        f"`{info['original_name']}` · extrait le **{fmt_date(day)}** ({info['extraction_how']}) · "
        f"déposé le {info['deposited_at'][:16].replace('T', ' à ')}"
    )
    if day is None:
        c1, c2 = st.columns([2, 1])
        chosen = c1.date_input("Date d'extraction", value=None, key=f"confirm_{kind}")
        if c2.button("Confirmer", key=f"confirm_btn_{kind}") and chosen:
            workspace().set_extraction_date(kind, chosen)
            reset_all()
            st.rerun()


def render() -> None:
    st.header("Dépôt du jour")
    ws = workspace()
    depot = ws.latest_depot()
    c1, c2 = st.columns(2)
    with c1:
        _file_card(STOCK, depot[STOCK][1] if STOCK in depot else None)
    with c2:
        _file_card(MOVES, depot[MOVES][1] if MOVES in depot else None)

    st.divider()
    if not ws.has_masters():
        st.warning("Référentiels manquants : multiples, conversions et boutiques/DC.")
        st.button("Ouvrir les référentiels", on_click=go, args=(REFS,))
        return
    if STOCK not in depot or MOVES not in depot:
        return

    data = day_data()
    if data is None:
        st.error(ss.day_error or "Fichiers illisibles.")
        return

    st.markdown("#### Contrôles qualité")
    moves = data.movements
    dates = sorted({m.movement_date for m in moves})
    locations = {line.location for line in data.stock}
    weeks = (len(dates) and ((dates[-1] - dates[0]).days + 1) // 7) or 0
    m = st.columns(4)
    m[0].metric("Lignes de stock", fmt_int(len(data.stock)))
    m[1].metric("Emplacements", fmt_int(len(locations)))
    m[2].metric("Mouvements", fmt_int(len(moves)))
    m[3].metric("Historique", f"{len(dates)} j" + (f" ({weeks} sem.)" if weeks else ""))
    if dates:
        st.caption(f"Mouvements du {fmt_date(dates[0])} au {fmt_date(dates[-1])}")
        if weeks < 4:
            st.warning("Moins de 4 semaines de mouvements : l'historique de secours des SKU "
                       "dormants sera incomplet.")  # fmt: skip

    issues = [row for rep in data.reports for row in rep.as_rows()]
    errors = [i for i in issues if i["niveau"] == "ERROR"]
    warnings = [i for i in issues if i["niveau"] == "WARNING"]
    if errors:
        st.error(f"{len(errors)} erreur(s) bloquante(s) à corriger avant de continuer.")
    elif warnings:
        st.warning(f"{len(warnings)} point(s) à vérifier.")
    else:
        st.success("Aucun problème détecté.")
    if issues:
        with st.expander(f"Détail des contrôles ({len(issues)})", expanded=bool(errors)):
            st.dataframe(
                pd.DataFrame(issues).drop(columns=["code"]), hide_index=True, width="stretch"
            )

    st.button("Prêt", type="primary", disabled=bool(errors), on_click=go, args=(ORDER,),
              key="ready")  # fmt: skip
