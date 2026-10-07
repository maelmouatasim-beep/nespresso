"""Supply Planning Copilot — poste de travail du planner.

Lancer :  streamlit run app/ui/main.py

Toutes les quantités viennent du moteur Python (app/engines). L'interface collecte les
paramètres, affiche, enregistre et exporte ; elle ne calcule aucune quantité.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingestion.depot import freshness  # noqa: E402
from app.ui.common import (  # noqa: E402
    DAY,
    DEPOT,
    EXPORT,
    FRESH_ICON,
    HISTORY,
    ORDER,
    PAGES,
    REFS,
    init_state,
    reset_all,
    run_date,
    ss,
    workspace,
)
from app.ui.screens import day, depot, export, history, order, referentials  # noqa: E402

st.set_page_config(page_title="Supply Planning Copilot", page_icon="📦", layout="wide")
st.markdown(
    """<style>
    .block-container {padding-top: 3.2rem; padding-bottom: 2rem; max-width: 1500px;}
    [data-testid="stMetricValue"] {font-size: 1.6rem; font-variant-numeric: tabular-nums;}
    h4 {margin-top: 0.8rem;}
    </style>""",
    unsafe_allow_html=True,
)

init_state()

with st.sidebar:
    st.markdown("### Supply Planning Copilot")
    st.text_input("Planner", key="author", placeholder="Ton nom")
    st.radio("Données", ["Mes données", "Démo B80"], key="ws_kind", horizontal=True,
             on_change=reset_all)  # fmt: skip
    st.radio("Écran", PAGES, key="page", label_visibility="collapsed")
    depot_info = workspace().latest_depot()
    if depot_info:
        days = [info.get("extraction") for _, info in depot_info.values()]
        oldest = min((date.fromisoformat(d) for d in days if d), default=None)
        fresh = freshness(oldest, run_date())
        st.caption(f"{FRESH_ICON[fresh.level]} {fresh.label}")
    else:
        st.caption("⚪ Aucun export déposé")

SCREENS = {
    DEPOT: depot.render,
    ORDER: order.render,
    DAY: day.render,
    EXPORT: export.render,
    REFS: referentials.render,
    HISTORY: history.render,
}
SCREENS[ss.page]()
