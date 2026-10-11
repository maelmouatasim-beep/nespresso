"""Écran 4 — Export : fichier LT, récapitulatif boutique, export Excel détaillé."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from app.engines.export import lt_content, lt_filename, recap_csv
from app.exports.files import order_workbook
from app.storage import db
from app.ui.common import (
    ORDER,
    author,
    boutique_label,
    current,
    day_data,
    fmt_1,
    fmt_date,
    fmt_int,
    go,
    ss,
    workspace,
)


def _save(result) -> int | None:  # noqa: ANN001
    if not author():
        return None
    if ss.saved_run_id:
        return ss.saved_run_id
    conn = db.connect(workspace().db_path)
    try:
        ss.saved_run_id = db.save_run(conn, result, author=author(), sources=day_data().source_hashes,
                                      overrides=ss.overrides.values())  # fmt: skip
    finally:
        conn.close()
    return ss.saved_run_id


def _on_lt(result, filename: str) -> None:  # noqa: ANN001
    run_id = _save(result)
    if run_id:
        conn = db.connect(workspace().db_path)
        try:
            db.mark_exported(conn, run_id, filename)
        finally:
            conn.close()


def render() -> None:
    st.header("Export")
    res = current()
    if res is None:
        st.info("Calcule d'abord une commande.")
        st.button("Aller à la commande boutique", on_click=go, args=(ORDER,))
        return
    p, s = res.params, res.summary
    names = day_data().masters.boutique_names
    now = datetime.now()
    name = lt_filename(p.boutique, now)
    st.markdown(f"**{boutique_label(p.boutique, names)}** · livraison **{fmt_date(p.delivery_date)}** · "
                f"{fmt_int(s.lines_ordered)} lignes · {fmt_int(s.units_total)} unités · "
                f"{fmt_1(res.pallets.total_pallets)} palettes")  # fmt: skip
    st.markdown(f"Fichier LT : `{name}` · généré le {now:%d/%m/%Y à %H:%M}")
    unseen = [x for x in res.lines if x.status.value != "OK" and x.sku not in ss.seen]
    if unseen:
        st.warning(f"{len(unseen)} ligne(s) REVIEW ou BLOCKED pas encore marquée(s) « Vu ».")
    if not author():
        st.error("Indique ton nom dans la barre de gauche pour exporter.")
    c = st.columns(3)
    c[0].download_button("Fichier LT (SKU;QTY)", data=lt_content(res.lines), file_name=name,
                         mime="text/csv", type="primary", key="dl_lt", disabled=not author(),
                         on_click=_on_lt, args=(res, name), width="stretch")  # fmt: skip
    c[1].download_button("Récapitulatif boutique",
                         data=recap_csv(p.boutique, p.delivery_date.isoformat(), res.lines, res.pallets),
                         file_name=f"Recap {name}", mime="text/csv", key="dl_recap",
                         disabled=not author(), width="stretch")  # fmt: skip
    c[2].download_button("Export Excel détaillé",
                         data=order_workbook(res, author=author(), sources=day_data().source_hashes,
                                             overrides=ss.overrides.values()),
                         file_name=f"Detail {name[:-4]}.xlsx",
                         mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         key="dl_xlsx", disabled=not author(), width="stretch")  # fmt: skip
    if ss.saved_run_id:
        st.success(f"Run n° {ss.saved_run_id} enregistré dans l'historique.")
    elif st.button("Enregistrer sans exporter", key="save_run", disabled=not author()):
        _save(res)
        st.rerun()
    st.caption("Le fichier LT s'importe à la main dans Nessoft : l'outil n'y écrit jamais.")
