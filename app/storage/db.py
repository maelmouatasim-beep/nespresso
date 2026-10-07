"""Stockage local SQLite : runs, recommandations et overrides (audit).

La base vit dans `data/` (jamais versionnée). Tout est en clair et lisible avec
n'importe quel outil SQLite.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.models import Override
from app.services.planning import PlanningResult

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "copilot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    author TEXT NOT NULL,
    boutique TEXT NOT NULL,
    dc TEXT NOT NULL,
    run_date TEXT NOT NULL,
    delivery_date TEXT NOT NULL,
    cover_days REAL NOT NULL,
    coffee_cover_days REAL,
    mode TEXT NOT NULL,
    rule_versions TEXT NOT NULL,
    sources TEXT NOT NULL,
    summary TEXT NOT NULL,
    exported_at TEXT,
    lt_filename TEXT
);
CREATE TABLE IF NOT EXISTS recommendations (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    sku TEXT NOT NULL,
    description TEXT,
    product_type TEXT,
    qty INTEGER NOT NULL,
    forecast_qty INTEGER,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    reasons TEXT NOT NULL,
    explanation TEXT NOT NULL,
    PRIMARY KEY (run_id, sku)
);
CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    sku TEXT NOT NULL,
    qty_before INTEGER NOT NULL,
    qty_after INTEGER NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    reason TEXT NOT NULL CHECK (length(trim(reason)) >= 3),
    author TEXT NOT NULL CHECK (length(trim(author)) >= 1),
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_boutique ON runs(boutique, id);
"""


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Ouvre la base (variable d'environnement COPILOT_DB, sinon data/copilot.db)."""
    path = path or Path(os.environ.get("COPILOT_DB", DEFAULT_DB))
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Met à jour une base créée par une version précédente (ajout de colonnes)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(overrides)")}
    if "category" not in cols:
        with conn:
            conn.execute("ALTER TABLE overrides ADD COLUMN category TEXT NOT NULL DEFAULT 'other'")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_run(
    conn: sqlite3.Connection,
    result: PlanningResult,
    *,
    author: str,
    sources: dict[str, str],
    overrides: Iterable[Override] = (),
) -> int:
    """Enregistre un run complet (paramètres, lignes finales, overrides). Retourne son id."""
    if not author.strip():
        raise ValueError("Le nom du planner est obligatoire pour enregistrer un run")
    p = result.params
    s = result.summary
    with conn:
        cur = conn.execute(
            """INSERT INTO runs (created_at, author, boutique, dc, run_date, delivery_date,
                cover_days, coffee_cover_days, mode, rule_versions, sources, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now(),
                author.strip(),
                p.boutique,
                result.dc,
                p.run_date.isoformat(),
                p.delivery_date.isoformat(),
                p.cover_days,
                p.coffee_cover_days,
                result.mode.value,
                json.dumps(result.rule_versions),
                json.dumps(sources),
                json.dumps(
                    {
                        "lines_total": s.lines_total,
                        "lines_ordered": s.lines_ordered,
                        "units_total": s.units_total,
                        "by_status": s.by_status,
                        "by_source": s.by_source,
                        "pallets": round(result.pallets.total_pallets, 2),
                        "rule_warnings": result.rule_warnings,
                    }
                ),
            ),  # fmt: skip
        )
        run_id = int(cur.lastrowid or 0)
        conn.executemany(
            """INSERT INTO recommendations (run_id, sku, description, product_type, qty,
                forecast_qty, source, status, reasons, explanation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id,
                    x.sku,
                    x.description,
                    x.product_type,
                    x.qty,
                    x.forecast_qty,
                    x.source.value,
                    x.status.value,
                    json.dumps(x.reasons, ensure_ascii=False),
                    json.dumps(x.explanation, ensure_ascii=False, default=str),
                )  # fmt: skip
                for x in result.lines
            ],
        )
        conn.executemany(
            """INSERT INTO overrides (run_id, sku, qty_before, qty_after, category, reason,
                author, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id,
                    o.sku,
                    o.qty_before,
                    o.qty_after,
                    o.category.value,
                    o.reason.strip(),
                    o.author.strip(),
                    o.timestamp.isoformat(),
                )  # fmt: skip
                for o in overrides
            ],
        )
    return run_id


def mark_exported(conn: sqlite3.Connection, run_id: int, lt_filename: str) -> None:
    with conn:
        conn.execute(
            "UPDATE runs SET exported_at = ?, lt_filename = ? WHERE id = ?",
            (_now(), lt_filename, run_id),
        )


def list_runs(conn: sqlite3.Connection, boutique: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM runs" + (" WHERE boutique = ?" if boutique else "") + " ORDER BY id DESC"
    rows = conn.execute(sql, (boutique,) if boutique else ()).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("rule_versions", "sources", "summary"):
            d[k] = json.loads(d[k])
        out.append(d)
    return out


def run_quantities(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    rows = conn.execute("SELECT sku, qty FROM recommendations WHERE run_id = ?", (run_id,))
    return {r["sku"]: r["qty"] for r in rows}


def run_overrides(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM overrides WHERE run_id = ? ORDER BY id", (run_id,))
    return [dict(r) for r in rows]


def previous_run_id(
    conn: sqlite3.Connection, boutique: str, before_id: int | None = None
) -> int | None:
    sql = "SELECT id FROM runs WHERE boutique = ?" + (" AND id < ?" if before_id else "")
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1",
                       (boutique, before_id) if before_id else (boutique,)).fetchone()  # fmt: skip
    return int(row["id"]) if row else None


def override_stats(conn: sqlite3.Connection, boutique: str | None = None) -> list[dict[str, Any]]:
    """Overrides de tous les runs (le plus récent d'abord), pour analyser les décisions."""
    sql = """SELECT o.*, r.boutique, r.delivery_date, r.cover_days FROM overrides o
             JOIN runs r ON r.id = o.run_id"""
    rows = conn.execute(
        sql + (" WHERE r.boutique = ?" if boutique else "") + " ORDER BY o.id DESC",
        (boutique,) if boutique else (),
    )
    return [dict(r) for r in rows]
