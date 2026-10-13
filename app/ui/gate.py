"""Traces des validations (qui, quand) et demandes boutique à terminer après l'export.

Les lignes à confirmer avant le fichier LT (REVIEW, BLOCKED, flaguées par un événement) sont
listées dans la page Commandes (D-043) ; chaque confirmation est écrite dans la base.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from app.domain.events import SkuEvent
from app.engines.events import store_requests_done
from app.storage import events as store
from app.ui.common import author, db_conn, ss, today


def trace(boutique: str, delivery: date, kind: str, skus: list[str], note: str = "") -> None:
    with db_conn() as conn:
        store.record_validation(conn, author=author() or "Planner non renseigné", boutique=boutique,
                                delivery=delivery, kind=kind, skus=skus, note=note)  # fmt: skip


def requests_after_export(events: list[SkuEvent]) -> None:
    ss.requests_to_close = list(dict.fromkeys(
        [*ss.requests_to_close, *(e.id for e in store_requests_done(events) if e.id)]
    ))  # fmt: skip


def _close_request(event_id: int, close: bool) -> None:
    ss.requests_to_close = [i for i in ss.requests_to_close if i != event_id]
    if close:
        with db_conn() as conn:
            store.stop_event(conn, event_id, author() or "Planner non renseigné", today(),
                             "commande exportée")  # fmt: skip


def close_requests_prompt() -> None:
    """Après l'export : chaque demande boutique propose de se terminer (confirmation)."""
    if not ss.requests_to_close:
        return
    with db_conn() as conn:
        pending = [store.get_event(conn, i) for i in ss.requests_to_close]
    for e in pending:
        if e is None:
            continue
        c1, c2, c3 = st.columns([4, 1, 1], vertical_alignment="center")
        c1.info(f"Demande boutique « {e.title} » ({', '.join(e.sku_codes())}) exportée : "
                "la terminer ?")  # fmt: skip
        c2.button("Terminer", key=f"close_req_{e.id}", type="primary", on_click=_close_request,
                  args=(e.id, True), width="stretch")  # fmt: skip
        c3.button("Garder active", key=f"keep_req_{e.id}", on_click=_close_request,
                  args=(e.id, False), width="stretch")  # fmt: skip
