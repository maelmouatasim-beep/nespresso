"""Pont entre la version web et le moteur Python, exécuté DANS le navigateur (Pyodide).

La page lit les fichiers Excel du planner dans le navigateur, puis appelle ces fonctions :
le calcul est fait par le même code Python que l'outil installé (`run_planning`). Rien
ne quitte le poste : il n'y a aucun appel réseau ici.

Chaque fonction prend et rend du texte JSON (simple à passer entre JavaScript et Python).
"""

from __future__ import annotations

import io
import json
from datetime import date
from typing import Any

from app.domain.models import (
    Allocation,
    Exclusion,
    ForecastMode,
    Launch,
    PlanningParameters,
    Product,
    ScheduleSlot,
    SkuConversion,
    StockMovement,
    StockSnapshotLine,
    TargetStock,
)
from app.engines.schedule import boutiques_ordering_on
from app.ingestion.depot import MOVES_KIND, STOCK_KIND, _header_found, date_from_filename
from app.ingestion.masters import Masters, all_boutiques, build_inputs_for_boutique
from app.ingestion.powerbi import (
    MOVEMENTS_SPEC,
    STOCK_SITUATION_SPEC,
    movements_from_records,
    stock_situation_from_records,
)
from app.ingestion.tables import (
    RawTable,
    ValidationReport,
    _cell_text,
    parse_table,
    read_raw,
)
from app.services.planning import BusinessInputs, run_planning
from app.web.serialize import Strings, line_meta, line_tuple

_STATE: dict[str, Any] = {}


# --- Référentiels (écrits par tools/build_demo_app.py) -------------------------------------


def masters_payload(
    masters: Masters, exclusions: list[Exclusion], examples: BusinessInputs
) -> dict:
    """Référentiels en JSON, pour la page web."""
    return {
        "products": [p.model_dump(mode="json") for p in masters.products.values()],
        "conversions": [c.model_dump(mode="json") for c in masters.conversions],
        "dc_mapping": masters.dc_mapping,
        "portfolio": masters.portfolio,
        "schedule": [s.model_dump(mode="json") for s in masters.schedule],
        "names": masters.boutique_names,
        "exclusions": [e.model_dump(mode="json") for e in exclusions],
        "examples": {
            "allocations": [a.model_dump(mode="json") for a in examples.allocations],
            "launches": [la.model_dump(mode="json") for la in examples.launches],
            "targets": [t.model_dump(mode="json") for t in examples.targets],
        },
    }


def init(payload: str) -> str:
    d = json.loads(payload)
    _STATE["masters"] = Masters(
        products={p["sku"]: Product.model_validate(p) for p in d["products"]},
        product_list_anomalies=[],
        conversions=[SkuConversion.model_validate(c) for c in d["conversions"]],
        dc_mapping=d["dc_mapping"],
        portfolio=d["portfolio"],
        schedule=[ScheduleSlot.model_validate(s) for s in d["schedule"]],
        boutique_names=d["names"],
    )
    _STATE["exclusions"] = [Exclusion.model_validate(e) for e in d["exclusions"]]
    ex = d["examples"]
    _STATE["examples"] = BusinessInputs(
        allocations=tuple(Allocation.model_validate(a) for a in ex["allocations"]),
        launches=tuple(Launch.model_validate(la) for la in ex["launches"]),
        targets=tuple(TargetStock.model_validate(t) for t in ex["targets"]),
    )
    return json.dumps({"products": len(_STATE["masters"].products)})


# --- Dépôt des exports -------------------------------------------------------------------


def _raw(rows: list[list[Any]]) -> RawTable:
    numeric = {
        (r, c)
        for r, row in enumerate(rows)
        for c, v in enumerate(row)
        if isinstance(v, int | float) and not isinstance(v, bool)
    }
    return RawTable(rows=[[_cell_text(v) for v in row] for row in rows], numeric_cells=numeric)


def ingest(rows_json: str, filename: str) -> str:
    """Classeur Excel déjà lu par la page (lignes de cellules) : reconnaît, lit, garde."""
    return _ingest(_raw(json.loads(rows_json)), filename)


def ingest_csv(text: str, filename: str) -> str:
    """Fichier CSV : lu ici en texte (les SKU ne deviennent jamais des nombres)."""
    return _ingest(read_raw(io.BytesIO(text.encode("utf-8")), filename), filename)


def _ingest(raw: RawTable, filename: str) -> str:
    head = RawTable(rows=raw.rows[:15], numeric_cells=raw.numeric_cells)
    kinds = [k for k, spec in ((STOCK_KIND, STOCK_SITUATION_SPEC), (MOVES_KIND, MOVEMENTS_SPEC))
             if _header_found(head, spec)]  # fmt: skip
    if len(kinds) != 1:
        return json.dumps({"kind": None, "error": "ni un Stock Situation ni un Stock Movements "
                           "(colonnes non reconnues)"})  # fmt: skip
    kind = kinds[0]
    if kind == STOCK_KIND:
        items, report = stock_situation_from_records(
            parse_table(raw, STOCK_SITUATION_SPEC, filename)
        )
    else:
        items, report = movements_from_records(parse_table(raw, MOVEMENTS_SPEC, filename))
    report.source = f"{'Stock Situation' if kind == STOCK_KIND else 'Stock Movements'} ({filename})"
    day = date_from_filename(filename)
    _STATE[kind] = {"items": items, "report": report, "name": filename, "day": day}
    return json.dumps({
        "kind": kind, "rows": len(items), "errors": report.has_errors,
        "day": day.isoformat() if day else None,
    })  # fmt: skip


def status(run_date_iso: str) -> str:
    """Contrôles qualité et liste des boutiques une fois les deux exports déposés."""
    run_date = date.fromisoformat(run_date_iso)
    stock: list[StockSnapshotLine] = _STATE.get(STOCK_KIND, {}).get("items", [])
    moves: list[StockMovement] = _STATE.get(MOVES_KIND, {}).get("items", [])
    reports: list[ValidationReport] = [
        _STATE[k]["report"] for k in (STOCK_KIND, MOVES_KIND) if k in _STATE
    ]
    masters: Masters = _STATE["masters"]
    dates = sorted({m.movement_date for m in moves})
    names = masters.boutique_names
    return json.dumps({
        "files": {k: {"name": _STATE[k]["name"], "day": _STATE[k]["day"].isoformat()
                      if _STATE[k]["day"] else None, "rows": len(_STATE[k]["items"])}
                  for k in (STOCK_KIND, MOVES_KIND) if k in _STATE},
        "quality": [row for rep in reports for row in rep.as_rows()],
        "errors": any(rep.has_errors for rep in reports),
        "dq": {"stock_rows": len(stock), "locations": len({x.location for x in stock}),
               "moves": len(moves), "days": len(dates)},
        "period": [dates[0].isoformat(), dates[-1].isoformat()] if dates else None,
        "boutiques": [[b, names.get(b, "")] for b in all_boutiques(masters, stock)]
        if stock else [],
        "planned": [[s.boutique, s.delivery_date.isoformat()]
                    for s in boutiques_ordering_on(masters.schedule, run_date)],
        "codes": {},
    })  # fmt: skip


# --- Calcul d'une commande -----------------------------------------------------------------


def plan(params_json: str) -> str:
    """Commande d'une boutique : mêmes fonctions que l'outil installé."""
    p = json.loads(params_json)
    masters: Masters = _STATE["masters"]
    if STOCK_KIND not in _STATE or MOVES_KIND not in _STATE:
        return json.dumps({"error": "Dépose d'abord les deux exports."})
    boutique = p["boutique"]
    try:
        inputs, dc = build_inputs_for_boutique(
            boutique, _STATE[STOCK_KIND]["items"], _STATE[MOVES_KIND]["items"],
            masters.products, masters.conversions, masters.dc_mapping,
            sources={"stock_situation": _STATE[STOCK_KIND]["name"],
                     "stock_movements": _STATE[MOVES_KIND]["name"]},
            history_days=int(p.get("history_days", 7)),
            fallback_weeks=int(p.get("fallback_weeks", 4)),
            portfolio=masters.portfolio.get(boutique, {}), exclusions=_STATE["exclusions"],
        )  # fmt: skip
        params = PlanningParameters(
            boutique=boutique, run_date=date.fromisoformat(p["run_date"]),
            delivery_date=date.fromisoformat(p["delivery"]), cover_days=float(p["cover"]),
            coffee_cover_days=None if p.get("coffee") is None else float(p["coffee"]),
        )  # fmt: skip
    except ValueError as exc:  # données absentes pour cette boutique, paramètre invalide
        return json.dumps({"error": f"Calcul impossible : {exc}"})
    business = _STATE["examples"] if p.get("examples") else BusinessInputs()
    res = run_planning(inputs, params, ForecastMode(p["mode"]), dc=dc, business_inputs=business)
    text = Strings()
    period = inputs.sales.period_start, inputs.sales.period_end
    return json.dumps({
        "lines": [line_meta(x, masters.products, dc) for x in res.lines],
        "res": [line_tuple(x, text) for x in res.lines],
        "text": text.items,
        "warnings": list(res.rule_warnings),
        "dc": dc,
        "history_days": res.history_days,
        "period": [d.isoformat() for d in period] if all(period) else None,
    })  # fmt: skip
