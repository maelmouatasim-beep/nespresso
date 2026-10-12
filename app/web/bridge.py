"""Pont entre la version web et le moteur Python, exécuté DANS le navigateur (Pyodide).

La page lit les fichiers Excel du planner dans le navigateur, puis appelle ces fonctions :
le calcul est fait par le même code Python que l'outil installé (`run_planning`). Rien
ne quitte le poste : il n'y a aucun appel réseau ici.

Chaque fonction prend et rend du texte JSON (simple à passer entre JavaScript et Python).
"""

from __future__ import annotations

import dataclasses
import io
import json
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.domain.events import SkuEvent
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
from app.ingestion import referential_checks as checks
from app.ingestion.business_inputs import read_allocations, read_launches, read_rules, read_targets
from app.ingestion.depot import MOVES_KIND, STOCK_KIND, _header_found, date_from_filename
from app.ingestion.exclusions import read_exclusions
from app.ingestion.fixtures_loader import (
    DC_MAPPING_COLUMNS,
    PORTFOLIO_COLUMNS,
    load_conversions,
    load_multiples,
    read_csv_as_text,
)
from app.ingestion.masters import (
    Masters,
    all_boutiques,
    build_inputs_for_boutique,
    load_boutique_names,
    load_schedule,
)
from app.ingestion.powerbi import (
    MOVEMENTS_SPEC,
    STOCK_SITUATION_SPEC,
    movements_from_records,
    stock_situation_from_records,
)
from app.ingestion.referential_edit import check_table
from app.ingestion.tables import (
    RawTable,
    ValidationReport,
    _cell_text,
    parse_table,
    read_raw,
)
from app.services.planning import BusinessInputs, EventContext, run_planning
from app.web.serialize import Strings, line_meta, line_tuple

_STATE: dict[str, Any] = {}
# Deux jeux d'exports gardés séparément : « demo » (données de test B80, recalculées quand
# le planner modifie un référentiel ou un événement) et « live » (« Mes fichiers »).
LIVE, DEMO = "live", "demo"


def _data(dataset: str) -> dict[str, Any]:
    return _STATE.setdefault("data", {}).setdefault(dataset, {})


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
    _STATE["business"] = {
        "allocations": tuple(Allocation.model_validate(a) for a in ex["allocations"]),
        "launches": tuple(Launch.model_validate(la) for la in ex["launches"]),
        "targets": tuple(TargetStock.model_validate(t) for t in ex["targets"]),
        "rules": (),
    }
    return json.dumps({"products": len(_STATE["masters"].products)})


# --- Référentiels modifiés dans la page ------------------------------------------------------

_BUSINESS_READERS = {
    "allocations": read_allocations,
    "launches": read_launches,
    "targets": read_targets,
    "rules": read_rules,
}


def _apply_table(key: str, csv_text: str) -> None:
    """Remplace un référentiel déjà vérifié par `check_table` (mêmes lecteurs qu'à l'import)."""
    masters: Masters = _STATE["masters"]
    if key in _BUSINESS_READERS:
        items, _ = _BUSINESS_READERS[key](io.BytesIO(csv_text.encode("utf-8")), f"{key}.csv")
        _STATE["business"][key] = tuple(items)
        return
    if key == "exclusions":
        exclusions, _ = read_exclusions(io.BytesIO(csv_text.encode("utf-8")), "exclusions.csv")
        _STATE["exclusions"] = exclusions
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{key}.csv"
        path.write_text(csv_text, encoding="utf-8")
        if key == "multiples":
            products, anomalies = load_multiples(path)
            update: dict[str, Any] = {"products": products, "product_list_anomalies": anomalies}
        elif key == "conversions":
            update = {"conversions": load_conversions(path)}
        elif key == "dc_mapping":
            rows = read_csv_as_text(path, DC_MAPPING_COLUMNS)
            update = {"dc_mapping": {r["boutique"]: r["dc"] for r in rows}}
        elif key == "portfolio":
            portfolio: dict[str, dict[str, str]] = {}
            for r in read_csv_as_text(path, PORTFOLIO_COLUMNS):
                portfolio.setdefault(r["boutique"], {})[r["sku"]] = r["filter_out"]
            update = {"portfolio": portfolio}
        elif key == "schedule":
            schedule, issues = load_schedule(path)
            update = {"schedule": schedule, "schedule_issues": issues,
                      "boutique_names": load_boutique_names(path)}  # fmt: skip
        else:
            raise ValueError(f"Référentiel inconnu : {key}")
    _STATE["masters"] = dataclasses.replace(masters, **update)


def _check(key: str, csv_text: str) -> tuple[list[str], list[str]]:
    if key == "exclusions":
        _, report = read_exclusions(io.BytesIO(csv_text.encode("utf-8")), "exclusions.csv")
        return [i.message for i in report.issues if i.level == "error"], []
    return check_table(key, csv_text)


def anomalies() -> dict[str, list[str]]:
    masters: Masters = _STATE["masters"]
    return {
        "multiples": checks.multiples_anomalies(masters),
        "conversions": checks.conversions_anomalies(masters),
        "portfolio": checks.portfolio_anomalies(masters),
        "dc_mapping": checks.dc_mapping_anomalies(masters),
        "schedule": list(masters.schedule_issues),
        "exclusions": checks.exclusions_anomalies(_STATE["exclusions"], masters),
    }


def set_tables(tables_json: str) -> str:
    """Référentiels modifiés par le planner ({clé: texte CSV}). Chaque table est relue
    comme à l'import ; une table en erreur n'est PAS appliquée (raison rendue)."""
    out: dict[str, dict[str, list[str]]] = {}
    for key, text in json.loads(tables_json).items():
        try:
            errors, warnings = _check(key, text)
            if not errors:
                _apply_table(key, text)
        except (ValueError, KeyError) as exc:
            errors, warnings = [str(exc)], []
        out[key] = {"errors": errors, "warnings": warnings}
    return json.dumps({"tables": out, "anomalies": anomalies()})


def check_event(event_json: str) -> str:
    """Événement saisi dans la page : mêmes règles que l'outil installé (SkuEvent)."""
    try:
        event = SkuEvent.model_validate(json.loads(event_json))
    except ValidationError as exc:
        msgs = [str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()]
        return json.dumps({"errors": msgs})
    return json.dumps({"errors": [], "event": event.model_dump(mode="json")})


# --- Dépôt des exports -------------------------------------------------------------------


def _raw(rows: list[list[Any]]) -> RawTable:
    numeric = {
        (r, c)
        for r, row in enumerate(rows)
        for c, v in enumerate(row)
        if isinstance(v, int | float) and not isinstance(v, bool)
    }
    return RawTable(rows=[[_cell_text(v) for v in row] for row in rows], numeric_cells=numeric)


def ingest(rows_json: str, filename: str, dataset: str = LIVE) -> str:
    """Classeur Excel déjà lu par la page (lignes de cellules) : reconnaît, lit, garde."""
    return _ingest(_raw(json.loads(rows_json)), filename, dataset)


def ingest_csv(text: str, filename: str, dataset: str = LIVE) -> str:
    """Fichier CSV : lu ici en texte (les SKU ne deviennent jamais des nombres)."""
    return _ingest(read_raw(io.BytesIO(text.encode("utf-8")), filename), filename, dataset)


def _ingest(raw: RawTable, filename: str, dataset: str = LIVE) -> str:
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
    _data(dataset)[kind] = {"items": items, "report": report, "name": filename, "day": day}
    return json.dumps({
        "kind": kind, "rows": len(items), "errors": report.has_errors,
        "day": day.isoformat() if day else None,
    })  # fmt: skip


def status(run_date_iso: str, dataset: str = LIVE) -> str:
    """Contrôles qualité et liste des boutiques une fois les deux exports déposés."""
    run_date = date.fromisoformat(run_date_iso)
    data = _data(dataset)
    stock: list[StockSnapshotLine] = data.get(STOCK_KIND, {}).get("items", [])
    moves: list[StockMovement] = data.get(MOVES_KIND, {}).get("items", [])
    reports: list[ValidationReport] = [
        data[k]["report"] for k in (STOCK_KIND, MOVES_KIND) if k in data
    ]
    masters: Masters = _STATE["masters"]
    dates = sorted({m.movement_date for m in moves})
    names = masters.boutique_names
    return json.dumps({
        "files": {k: {"name": data[k]["name"], "day": data[k]["day"].isoformat()
                      if data[k]["day"] else None, "rows": len(data[k]["items"])}
                  for k in (STOCK_KIND, MOVES_KIND) if k in data},
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
    data = _data(str(p.get("dataset") or LIVE))
    if STOCK_KIND not in data or MOVES_KIND not in data:
        return json.dumps({"error": "Dépose d'abord les deux exports."})
    boutique = p["boutique"]
    try:
        inputs, dc = build_inputs_for_boutique(
            boutique, data[STOCK_KIND]["items"], data[MOVES_KIND]["items"],
            masters.products, masters.conversions, masters.dc_mapping,
            sources={"stock_situation": data[STOCK_KIND]["name"],
                     "stock_movements": data[MOVES_KIND]["name"]},
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
    business = BusinessInputs(**_STATE["business"]) if p.get("examples") else BusinessInputs()
    # Événements SKU envoyés par la page : le moteur choisit lui-même ceux qui sont actifs
    # (date de livraison, boutique, planner, « Reprendre »).
    events = EventContext(
        tuple(SkuEvent.model_validate(e) for e in p.get("events", [])),
        str(p.get("planner") or ""), frozenset(int(i) for i in p.get("taken", [])),
    )  # fmt: skip
    res = run_planning(inputs, params, ForecastMode(p["mode"]), dc=dc, business_inputs=business,
                       event_context=events)  # fmt: skip
    text = Strings()
    period = inputs.sales.period_start, inputs.sales.period_end
    return json.dumps({
        "lines": [line_meta(x, masters.products, dc) for x in res.lines],
        "res": [line_tuple(x, text) for x in res.lines],
        "text": text.items,
        "warnings": list(res.rule_warnings),
        "dc": dc,
        "history_days": res.history_days,
        "events": [e.id for e in res.events],
        "period": [d.isoformat() for d in period] if all(period) else None,
    })  # fmt: skip
