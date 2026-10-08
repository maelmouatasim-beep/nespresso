"""Écran 6 — Historique : runs, modifications par catégorie, comparaison de deux runs."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import streamlit as st

from app.domain.models import OVERRIDE_CATEGORY_LABELS
from app.engines.compare import compare_orders
from app.storage import db
from app.ui.common import MODE_LABEL, workspace


def render() -> None:
    st.header("Historique")
    conn = db.connect(workspace().db_path)
    try:
        runs = db.list_runs(conn)
        if not runs:
            st.info("Aucune commande enregistrée pour l'instant.")
            return
        boutiques = sorted({r["boutique"] for r in runs})
        pick = st.multiselect("Boutiques", boutiques, default=[], placeholder="Toutes", key="h_btq")
        runs = [r for r in runs if not pick or r["boutique"] in pick]
        st.dataframe(
            pd.DataFrame([{
                "N°": r["id"], "Boutique": r["boutique"], "Créé le": r["created_at"][:16].replace("T", " "),
                "Par": r["author"], "Livraison": r["delivery_date"], "Cover": r["cover_days"],
                "Mode": MODE_LABEL.get(r["mode"], r["mode"]), "Lignes": r["summary"]["lines_ordered"],
                "Unités": r["summary"]["units_total"], "Fichier LT": r["lt_filename"] or "",
            } for r in runs]),
            hide_index=True, width="stretch",
            column_config={"Unités": st.column_config.NumberColumn(format="localized")},
        )  # fmt: skip

        st.subheader("Modifications des planners")
        labels = {c.value: label for c, label in OVERRIDE_CATEGORY_LABELS.items()}
        stats = [o for o in db.override_stats(conn) if not pick or o["boutique"] in pick]
        if not stats:
            st.info("Aucune modification enregistrée.")
        else:
            c1, c2 = st.columns(2)
            by_cat = Counter(labels.get(o["category"], "Sans catégorie") for o in stats)
            c1.dataframe(pd.DataFrame(by_cat.most_common(), columns=["Catégorie", "Modifications"]),
                         hide_index=True, width="stretch")  # fmt: skip
            by_sku = Counter(o["sku"] for o in stats)
            c2.dataframe(pd.DataFrame(by_sku.most_common(15), columns=["SKU le plus modifié", "Fois"]),
                         hide_index=True, width="stretch")  # fmt: skip
            with st.expander(f"Détail ({len(stats)})"):
                st.dataframe(pd.DataFrame([{
                    "Boutique": o["boutique"], "SKU": o["sku"], "Avant": o["qty_before"],
                    "Après": o["qty_after"], "Catégorie": labels.get(o["category"], "Sans catégorie"),
                    "Raison": o["reason"], "Par": o["author"], "Quand": o["timestamp"][:16],
                } for o in stats]), hide_index=True, width="stretch")  # fmt: skip

        st.subheader("Comparer deux commandes")
        ids = [r["id"] for r in runs]
        label = {
            r["id"]: f"n° {r['id']} · {r['boutique']} · {r['created_at'][:16].replace('T', ' ')}"
            for r in runs
        }
        c1, c2 = st.columns(2)
        a = c1.selectbox(
            "Commande A", ids, index=min(1, len(ids) - 1), format_func=label.get, key="cmp_a"
        )
        b = c2.selectbox("Commande B", ids, index=0, format_func=label.get, key="cmp_b")
        if a and b and a != b:
            diff = compare_orders(db.run_quantities(conn, a), db.run_quantities(conn, b))
            st.markdown(f"**{len(diff)} SKU dont la quantité change**")
            st.dataframe(pd.DataFrame(diff, columns=["sku", "avant", "maintenant", "écart"]).rename(
                columns={"sku": "SKU", "avant": "A", "maintenant": "B", "écart": "Écart"}),
                hide_index=True, width="stretch")  # fmt: skip
    finally:
        conn.close()
