"""Référentiel des exclusions (SKU à ne jamais envoyer) : lecture et écriture CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

from app.domain.models import Exclusion
from app.ingestion.tables import TableSpec, ValidationReport, parse_dates, parse_table, read_raw

EXCLUSION_COLUMNS = ["sku", "boutiques", "reason", "author", "updated_on"]
EXCLUSIONS_SPEC = TableSpec(
    name="Exclusions",
    columns={
        "sku": ("sku", "SKU"),
        "boutiques": ("boutiques", "Boutiques", "BTQ"),
        "reason": ("reason", "Raison"),
        "author": ("author", "Auteur"),
        "updated_on": ("updated_on", "Date", "Mis à jour le"),
    },
    required=frozenset({"sku", "reason", "author", "updated_on"}),
)


def parse_boutiques(text: str) -> tuple[str, ...]:
    """« ALL », « toutes » ou vide → toutes ; sinon codes séparés par ; , ou espace."""
    cleaned = text.replace(",", ";").replace(" ", ";")
    codes = tuple(c.strip().upper() for c in cleaned.split(";") if c.strip())
    return () if not codes or codes[0] in ("ALL", "TOUTES") else codes


def read_exclusions(
    source: Path | BinaryIO, filename: str
) -> tuple[list[Exclusion], ValidationReport]:
    parsed = parse_table(read_raw(source, filename), EXCLUSIONS_SPEC, filename)
    report = parsed.report
    if not parsed.records:
        return [], report
    dates, _, err = parse_dates([r["updated_on"] for r in parsed.records])
    if err:
        report.add("error", "BAD_DATE", err)
        return [], report
    out: list[Exclusion] = []
    for rec, day in zip(parsed.records, dates, strict=True):
        try:
            if day is None:
                raise ValueError("date de mise à jour attendue")
            out.append(
                Exclusion(
                    sku=rec["sku"],
                    boutiques=parse_boutiques(rec.get("boutiques", "")),
                    reason=rec["reason"],
                    author=rec["author"],
                    updated_on=day,
                )
            )
        except (ValueError, ValidationError) as exc:
            msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            report.add("error", "INVALID_ROW", f"Ligne {rec['_row']} : {msg}", [int(rec["_row"])])
    report.rows_kept = len(out)
    return out, report


def exclusions_csv(exclusions: Iterable[Exclusion]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(EXCLUSION_COLUMNS)
    for e in exclusions:
        w.writerow([e.sku, ";".join(e.boutiques) or "ALL", e.reason, e.author,
                    e.updated_on.isoformat()])  # fmt: skip
    return buf.getvalue()
