"""Planners et événements SKU dans la base locale (tables créées par `db.connect`).

Chaque création, modification, fin, annulation ou reprise est écrite dans
`sku_event_history` (qui, quand, quoi). Chaque validation avant export est écrite dans
`event_validations`. Rien n'est effacé.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from app.domain.events import EventStatus, SkuEvent

# Champs qui ne comptent pas comme une modification dans l'historique.
_META = {"id", "created_by", "created_at", "updated_at"}


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


# --- Planners -------------------------------------------------------------------------


def clean_planner_name(name: str) -> str:
    """Prénom seulement : pas d'email, pas de texte vide."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Le prénom est obligatoire")
    if "@" in name:
        raise ValueError("Prénom seulement, pas d'email")
    if len(name) > 40:
        raise ValueError("40 caractères au maximum")
    return name


def list_planners(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM planners WHERE active = 1 ORDER BY name COLLATE NOCASE")
    return [r["name"] for r in rows]


def add_planner(conn: sqlite3.Connection, name: str) -> str:
    name = clean_planner_name(name)
    with conn:
        conn.execute(
            "INSERT INTO planners (name, active, created_at) VALUES (?, 1, ?) "
            "ON CONFLICT(name) DO UPDATE SET active = 1",
            (name, _now().isoformat()),
        )
    return name


def remove_planner(conn: sqlite3.Connection, name: str) -> None:
    """Retire de la liste sans effacer (ses événements gardent son nom)."""
    with conn:
        conn.execute("UPDATE planners SET active = 0 WHERE name = ?", (name,))


# --- Événements -------------------------------------------------------------------------


def _payload(event: SkuEvent) -> dict[str, Any]:
    return event.model_dump(mode="json", exclude={"id"})


def _from_row(row: sqlite3.Row) -> SkuEvent:
    return SkuEvent.model_validate({**json.loads(row["payload"]), "id": row["id"]})


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    return {
        k: [before.get(k), after.get(k)]
        for k in sorted(set(before) | set(after))
        if k not in _META and before.get(k) != after.get(k)
    }


def _history(conn: sqlite3.Connection, event_id: int, author: str, action: str,
             changes: dict[str, Any], when: datetime) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO sku_event_history (event_id, at, author, action, changes) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_id, when.isoformat(), author.strip(), action,
         json.dumps(changes, ensure_ascii=False, default=str)),
    )  # fmt: skip


def get_event(conn: sqlite3.Connection, event_id: int) -> SkuEvent | None:
    row = conn.execute("SELECT * FROM sku_events WHERE id = ?", (event_id,)).fetchone()
    return _from_row(row) if row else None


def list_events(conn: sqlite3.Connection) -> list[SkuEvent]:
    return [_from_row(r) for r in conn.execute("SELECT * FROM sku_events ORDER BY id")]


def save_event(
    conn: sqlite3.Connection, event: SkuEvent, author: str, action: str | None = None
) -> SkuEvent:
    """Crée (id None) ou modifie un événement. Retourne l'événement enregistré."""
    if not author.strip():
        raise ValueError("Choisis ton nom (Planner) avant d'enregistrer un événement")
    when = _now()
    with conn:
        if event.id is None:
            saved = event.model_copy(
                update={
                    "created_by": event.created_by or author.strip(),
                    "created_at": when,
                    "updated_at": when,
                }  # fmt: skip
            )
            cur = conn.execute(
                "INSERT INTO sku_events (scope, type, status, planner, title, start, end, payload,"
                " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _columns(saved) + (json.dumps(_payload(saved), ensure_ascii=False),
                                   saved.created_by, when.isoformat(), when.isoformat()),
            )  # fmt: skip
            saved = saved.model_copy(update={"id": int(cur.lastrowid or 0)})
            _history(conn, saved.id or 0, author, action or "créé", {}, when)
            return saved
        before = get_event(conn, event.id)
        if before is None:
            raise ValueError(f"Événement {event.id} introuvable")
        saved = event.model_copy(
            update={
                "created_by": before.created_by,
                "created_at": before.created_at,
                "updated_at": when,
            }  # fmt: skip
        )
        changes = _diff(_payload(before), _payload(saved))
        if not changes:
            return before
        conn.execute(
            "UPDATE sku_events SET scope = ?, type = ?, status = ?, planner = ?, title = ?, "
            "start = ?, end = ?, payload = ?, updated_at = ? WHERE id = ?",
            _columns(saved) + (json.dumps(_payload(saved), ensure_ascii=False),
                               when.isoformat(), event.id),
        )  # fmt: skip
        _history(conn, event.id, author, action or "modifié", changes, when)
        return saved


def _columns(e: SkuEvent) -> tuple[Any, ...]:
    return (e.scope.value, e.type.value, e.status.value, e.planner, e.title, e.start.isoformat(),
            e.end.isoformat() if e.end else None)  # fmt: skip


STATUS_ACTIONS = {
    EventStatus.DONE: "terminé",
    EventStatus.CANCELLED: "annulé",
    EventStatus.ACTIVE: "activé",
    EventStatus.DRAFT: "remis en brouillon",
}


def set_status(
    conn: sqlite3.Connection, event_id: int, status: EventStatus, author: str, note: str = ""
) -> SkuEvent:
    event = get_event(conn, event_id)
    if event is None:
        raise ValueError(f"Événement {event_id} introuvable")
    action = STATUS_ACTIONS[status] + (f" ({note})" if note else "")
    return save_event(conn, event.model_copy(update={"status": status}), author, action)


def log_takeover(conn: sqlite3.Connection, event_id: int, author: str) -> None:
    """« Reprendre » : un collègue applique l'événement personnel à sa session (tracé)."""
    with conn:
        _history(conn, event_id, author, "repris", {}, _now())


def event_history(conn: sqlite3.Connection, event_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sku_event_history WHERE event_id = ? ORDER BY id DESC", (event_id,)
    )
    return [{**dict(r), "changes": json.loads(r["changes"])} for r in rows]


# --- Validations avant export -----------------------------------------------------------

VALIDATION_KINDS = ("seen", "confirm_block", "include", "close_request")


def record_validation(conn: sqlite3.Connection, *, author: str, boutique: str, delivery: date,
                      kind: str, skus: Iterable[str], note: str = "") -> None:  # fmt: skip
    if kind not in VALIDATION_KINDS:
        raise ValueError(f"Validation inconnue : {kind}")
    if not author.strip():
        raise ValueError("Choisis ton nom (Planner) pour valider")
    with conn:
        conn.execute(
            "INSERT INTO event_validations (at, author, boutique, delivery_date, kind, skus, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now().isoformat(), author.strip(), boutique, delivery.isoformat(), kind,
             json.dumps(list(skus), ensure_ascii=False), note),
        )  # fmt: skip


def list_validations(conn: sqlite3.Connection, boutique: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM event_validations" + (" WHERE boutique = ?" if boutique else "")
    rows = conn.execute(sql + " ORDER BY id DESC", (boutique,) if boutique else ())
    return [{**dict(r), "skus": json.loads(r["skus"])} for r in rows]
