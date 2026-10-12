"""Garde-fou avant export : trois points à mettre au vert (événements SKU).

1. Lignes BLOCKED par un événement : blocage confirmé ou ligne modifiée avec une raison.
2. SKU flagués par un événement : marqués « Vu » (ou modifiés). « Marquer comme vus » en
   un clic n'est actif qu'une fois le filtre « Événements » de la commande ouvert (Q-030).
3. Lancements, urgences et demandes boutique à inclure : case cochée.
Chaque validation est écrite dans la base (qui, quand).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import streamlit as st

from app.domain.events import SkuEvent
from app.engines.events import ExportCheck, store_requests_done
from app.storage import events as store
from app.ui.common import author, db_conn, events_viewed, ss

OK_ICON = ":green[:material/check_circle:]"
BAD_ICON = ":red[:material/block:]"
TODO_ICON = ":orange[:material/pending:]"
MAX_LISTED = 12


@dataclass(frozen=True)
class GateRow:
    boutique: str
    delivery: date
    check: ExportCheck


def trace(boutique: str, delivery: date, kind: str, skus: list[str], note: str = "") -> None:
    with db_conn() as conn:
        store.record_validation(conn, author=author() or "Planner non renseigné", boutique=boutique,
                                delivery=delivery, kind=kind, skus=skus, note=note)  # fmt: skip


def _listed(rows: list[GateRow], get: Callable[[ExportCheck], tuple]) -> str:
    parts = []
    for r in rows:
        items = [x if isinstance(x, str) else f"{x[0]} ({x[1]})" for x in get(r.check)]
        if items:
            more = f" … (+{len(items) - MAX_LISTED})" if len(items) > MAX_LISTED else ""
            prefix = f"{r.boutique} : " if len(rows) > 1 else ""
            parts.append(prefix + ", ".join(items[:MAX_LISTED]) + more)
    return " · ".join(parts)


def render_gate(
    rows: list[GateRow],
    *,
    key: str,
    confirm_blocks: Callable[[GateRow], None],
    mark_seen: Callable[[GateRow], None],
    set_include: Callable[[GateRow, bool], None],
) -> bool:
    """Affiche les 3 points. Retourne True quand tout est vert (export permis)."""
    blocked = sum(len(r.check.blocked) for r in rows)
    unseen = sum(len(r.check.unvalidated) for r in rows)
    include = sum(len(r.check.to_include) for r in rows)
    include_ok = all(r.check.include_ok for r in rows)
    with st.container(border=True, key=f"gate_{key}"):
        st.markdown("**Vérification avant export (événements SKU)**")
        c1, c2 = st.columns([4, 1.4], vertical_alignment="center")
        c1.markdown(f"{OK_ICON if not blocked else BAD_ICON} **{blocked} ligne(s) BLOCKED par un "
                    "événement**" + (f"  \n{_listed(rows, lambda c: c.blocked)}" if blocked else ""))  # fmt: skip
        if blocked and c2.button("Confirmer les blocages", key=f"{key}_blocks", width="stretch",
                                 help="La quantité reste 0 (tracé). Pour envoyer quand même : "
                                      "modifier la ligne avec une raison."):  # fmt: skip
            for r in rows:
                if r.check.blocked:
                    confirm_blocks(r)
            st.rerun()
        c1, c2 = st.columns([4, 1.4], vertical_alignment="center")
        c1.markdown(f"{OK_ICON if not unseen else TODO_ICON} **{unseen} SKU flagué(s) par un "
                    "événement non encore validé(s)**"
                    + (f"  \n{_listed(rows, lambda c: c.unvalidated)}" if unseen else ""))  # fmt: skip
        to_open = [r.boutique for r in rows
                   if r.check.unvalidated and not events_viewed(r.boutique, r.delivery)]  # fmt: skip
        if to_open:
            where = f" ({', '.join(to_open)})" if len(rows) > 1 else ""
            c1.caption(f":material/info: Ouvre d'abord le filtre « Événements » de la commande"
                       f"{where} (Commande boutique) pour activer « Marquer comme vus ».")  # fmt: skip
        if unseen and c2.button("Marquer comme vus", key=f"{key}_seen", width="stretch",
                                disabled=bool(to_open),
                                help="Actif une fois le filtre « Événements » de la commande "
                                     "ouvert au moins une fois."):  # fmt: skip
            for r in rows:
                if r.check.unvalidated:
                    mark_seen(r)
            st.rerun()
        if not include:
            st.markdown(f"{OK_ICON} **Aucun lancement ni demande boutique à inclure**")
        else:
            st.markdown(f"{OK_ICON if include_ok else TODO_ICON} **Lancements et demandes boutique "
                        f"à inclure** : {_listed(rows, lambda c: c.to_include)}")  # fmt: skip
            value = st.checkbox("J'ai vérifié : ces lancements et demandes sont inclus",
                                value=include_ok, key=f"{key}_include_{include}")  # fmt: skip
            if value != include_ok:
                for r in rows:
                    if r.check.to_include:
                        set_include(r, value)
                st.rerun()
        ok = not blocked and not unseen and include_ok
        if not ok:
            st.caption("Le téléchargement est désactivé tant que ces points ne sont pas verts.")
    return ok


def requests_after_export(events: list[SkuEvent]) -> None:
    ss.requests_to_close = list(dict.fromkeys(
        [*ss.requests_to_close, *(e.id for e in store_requests_done(events) if e.id)]
    ))  # fmt: skip


def _close_request(event_id: int, close: bool) -> None:
    ss.requests_to_close = [i for i in ss.requests_to_close if i != event_id]
    if close:
        from app.domain.events import EventStatus

        with db_conn() as conn:
            store.set_status(conn, event_id, EventStatus.DONE,
                             author() or "Planner non renseigné", "commande exportée")  # fmt: skip


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
