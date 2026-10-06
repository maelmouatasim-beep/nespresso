"""Lecture des modèles simples remplis par l'équipe : allocations, lancements, stock cible.

Les modèles vierges sont générés par `tools/make_templates.py` dans `templates/`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

from app.domain.models import Allocation, BoutiqueRule, Launch, TargetStock
from app.ingestion.tables import (
    TableSpec,
    ValidationReport,
    parse_dates,
    parse_number,
    parse_table,
    read_raw,
)

ALLOCATIONS_SPEC = TableSpec(
    name="Allocations",
    columns={
        "boutique": ("boutique", "BTQ"),
        "sku": ("sku", "SKU"),
        "total_qty": ("total_qty", "Total", "Quantité totale"),
        "already_sent": ("already_sent", "Déjà envoyé", "Deja envoye"),
        "wave_plan": ("wave_plan", "Vagues", "Waves"),
        "current_wave": ("current_wave", "Vague en cours", "Current wave"),
        "comment": ("comment", "Commentaire"),
    },
    required=frozenset({"boutique", "sku", "total_qty", "wave_plan", "current_wave"}),
)
LAUNCHES_SPEC = TableSpec(
    name="Lancements",
    columns={
        "boutique": ("boutique", "BTQ"),
        "sku": ("sku", "SKU"),
        "launch_date": ("launch_date", "Date de lancement", "Launch date"),
        "qty": ("qty", "Quantité", "Quantite"),
        "comment": ("comment", "Commentaire"),
    },
    required=frozenset({"boutique", "sku", "launch_date", "qty"}),
)
TARGETS_SPEC = TableSpec(
    name="Stock cible",
    columns={
        "boutique": ("boutique", "BTQ"),
        "sku": ("sku", "SKU"),
        "min_qty": ("min_qty", "Min"),
        "target_qty": ("target_qty", "Cible", "Target"),
        "max_qty": ("max_qty", "Max"),
    },
    required=frozenset({"boutique", "sku", "min_qty", "target_qty", "max_qty"}),
)
RULES_SPEC = TableSpec(
    name="Règles boutique",
    columns={
        "boutique": ("boutique", "BTQ"),
        "rule": ("rule", "Règle", "Regle"),
        "sku": ("sku", "SKU"),
        "value": ("value", "Valeur"),
        "comment": ("comment", "Commentaire"),
    },
    required=frozenset({"boutique", "rule", "value"}),
    sku_fields=frozenset(),
)


def _int(text: str) -> int | None:
    n = parse_number(text)
    return int(n) if n is not None and n.is_integer() else None


def _err(report: ValidationReport, row: str, exc: Exception) -> None:
    msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
    report.add("error", "INVALID_ROW", f"Ligne {row} : {msg}", [int(row)])


def read_allocations(
    source: Path | BinaryIO, filename: str
) -> tuple[list[Allocation], ValidationReport]:
    parsed = parse_table(read_raw(source, filename), ALLOCATIONS_SPEC, filename)
    out: list[Allocation] = []
    for rec in parsed.records:
        try:
            waves = tuple(int(w) for w in rec["wave_plan"].replace(" ", "").split("/") if w)
            total, current = _int(rec["total_qty"]), _int(rec["current_wave"])
            sent = _int(rec.get("already_sent") or "0")
            if total is None or current is None or sent is None:
                raise ValueError("nombre entier attendu (total, déjà envoyé, vague)")
            out.append(
                Allocation(
                    boutique=rec["boutique"],
                    sku=rec["sku"],
                    total_qty=total,
                    already_sent=sent,
                    wave_plan=waves,
                    current_wave=current,
                    comment=rec.get("comment") or None,
                )  # fmt: skip
            )
        except (ValueError, ValidationError) as exc:
            _err(parsed.report, rec["_row"], exc)
    parsed.report.rows_kept = len(out)
    return out, parsed.report


def read_launches(source: Path | BinaryIO, filename: str) -> tuple[list[Launch], ValidationReport]:
    parsed = parse_table(read_raw(source, filename), LAUNCHES_SPEC, filename)
    report = parsed.report
    if not parsed.records:
        return [], report
    dates, _, err = parse_dates([r["launch_date"] for r in parsed.records])
    if err:
        report.add("error", "BAD_DATE", err)
        return [], report
    out: list[Launch] = []
    for rec, day in zip(parsed.records, dates, strict=True):
        try:
            qty = _int(rec["qty"])
            if qty is None or not isinstance(day, date):
                raise ValueError("quantité entière et date attendues")
            out.append(
                Launch(
                    boutique=rec["boutique"],
                    sku=rec["sku"],
                    launch_date=day,
                    qty=qty,
                    comment=rec.get("comment") or None,
                )  # fmt: skip
            )
        except (ValueError, ValidationError) as exc:
            _err(report, rec["_row"], exc)
    report.rows_kept = len(out)
    return out, report


def read_targets(
    source: Path | BinaryIO, filename: str
) -> tuple[list[TargetStock], ValidationReport]:
    parsed = parse_table(read_raw(source, filename), TARGETS_SPEC, filename)
    out: list[TargetStock] = []
    for rec in parsed.records:
        try:
            nums = {f: parse_number(rec[f]) for f in ("min_qty", "target_qty", "max_qty")}
            if any(v is None for v in nums.values()):
                raise ValueError("min, cible et max doivent être des nombres")
            out.append(TargetStock(boutique=rec["boutique"], sku=rec["sku"], **nums))
        except (ValueError, ValidationError) as exc:
            _err(parsed.report, rec["_row"], exc)
    parsed.report.rows_kept = len(out)
    return out, parsed.report


def read_rules(
    source: Path | BinaryIO, filename: str
) -> tuple[list[BoutiqueRule], ValidationReport]:
    parsed = parse_table(read_raw(source, filename), RULES_SPEC, filename)
    out: list[BoutiqueRule] = []
    for rec in parsed.records:
        if not rec.get("boutique"):
            continue
        try:
            value = parse_number(rec["value"])
            if value is None:
                raise ValueError("valeur numérique attendue")
            out.append(
                BoutiqueRule(
                    boutique=rec["boutique"],
                    rule=rec["rule"].strip().lower(),
                    sku=rec.get("sku") or None,
                    value=value,
                    comment=rec.get("comment") or None,
                )
            )
        except (ValueError, ValidationError) as exc:
            _err(parsed.report, rec["_row"], exc)
    parsed.report.rows_kept = len(out)
    return out, parsed.report
