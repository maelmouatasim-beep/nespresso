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

st.set_page_config(
    page_title="Supply Planning Copilot", page_icon=":material/local_cafe:", layout="wide"
)
# Charte visuelle (docs/charte_visuelle.md) : thème clair uniquement, couleurs dans
# .streamlit/config.toml ; ici seulement ce que le thème ne règle pas.
st.markdown(
    """<style>
    :root { color-scheme: light; }
    .block-container {padding-top: 2.6rem; padding-bottom: 2rem; max-width: 1500px;}
    [data-testid="stMainMenu"], [data-testid="stAppDeployButton"] {display: none;}
    /* Tuiles : carte blanche arrondie, grande valeur, petit libellé */
    [data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E8E1D8; border-radius: 10px;
        padding: 12px 16px; box-shadow: 0 1px 3px rgba(43, 29, 20, 0.06);}
    [data-testid="stMetricLabel"] p {font-size: 0.82rem; color: #6B5B4E;}
    [data-testid="stMetricValue"] {font-size: 1.75rem; font-variant-numeric: tabular-nums; color: #2B1D14;}
    /* Titres de section : accent doré (teinte foncée lisible sur crème) */
    h3, h4 {color: #8A6A3A; font-weight: 600;}
    /* Bouton principal doré : texte brun foncé (contraste 5,3:1) */
    [data-testid="stBaseButton-primary"] {color: #2B1D14; border-color: #B08D57;}
    [data-testid="stBaseButton-primary"]:hover {background: #C29F69; border-color: #C29F69; color: #2B1D14;}
    [data-testid="stBaseButton-secondary"]:hover {border-color: #B08D57; color: #8A6A3A;}
    /* Cartes encadrées (panneau « Pourquoi ») : blanc + ombre légère */
    .st-key-why_panel {background: #FFFFFF; box-shadow: 0 2px 8px rgba(43, 29, 20, 0.08);}
    /* Filtres en boutons : un peu plus compacts */
    [data-testid="stButtonGroup"] button p {font-size: 0.86rem;}
    button[data-variant="segmented_control"][aria-checked="true"] {background: #F3E9D8 !important;
        border-color: #B08D57 !important;}
    button[data-variant="segmented_control"][aria-checked="true"],
    button[data-variant="segmented_control"][aria-checked="true"] * {color: #2B1D14 !important;
        font-weight: 600;}
    button[data-variant="segmented_control"]:hover {border-color: #B08D57;}
    /* Logo texte de la barre latérale */
    .spc-logo {font-weight: 700; font-size: 1.15rem; letter-spacing: 0.01em; color: #FAF7F2;
        line-height: 1.25; margin: -0.6rem 0 1rem;}
    .spc-logo span {display: block; font-weight: 400; font-size: 0.78rem; color: #D8C9B4; margin-top: 4px;}
    .spc-logo b {color: #B08D57;}
    </style>""",
    unsafe_allow_html=True,
)

init_state()

with st.sidebar:
    st.markdown('<div class="spc-logo">Supply Planning <b>Copilot</b>'
                "<span>Boutique Planning · Nespresso Canada</span></div>",
                unsafe_allow_html=True)  # fmt: skip
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
        st.caption(f"{FRESH_ICON['unknown']} Aucun export déposé")

SCREENS = {
    DEPOT: depot.render,
    ORDER: order.render,
    DAY: day.render,
    EXPORT: export.render,
    REFS: referentials.render,
    HISTORY: history.render,
}
SCREENS[ss.page]()
