"""Génère la version web de démonstration de l'outil (données de test B80).

Le service Python complet (`run_planning` : sélection, prévision, arbitrage métier)
est exécuté ICI pour toutes les combinaisons proposées dans la page (mode, jours de
couverture, avec / sans exemples d'entrées métier). La page HTML ne calcule aucune
quantité : elle affiche le résultat qui correspond aux paramètres choisis, applique
les modifications saisies par le planner et fait les totaux.

    python tools/build_demo_app.py      # écrit preview/outil_B80.html
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models import (  # noqa: E402
    COFFEE_TYPE,
    Allocation,
    ForecastMode,
    Launch,
    PlanningParameters,
    TargetStock,
)
from app.engines.pallets import OTHERS, PER_PALLET, family  # noqa: E402
from app.ingestion.masters import build_inputs_for_boutique  # noqa: E402
from app.ingestion.powerbi import check_freshness  # noqa: E402
from app.ingestion.tables import ValidationReport  # noqa: E402
from app.services.planning import RULE_VERSIONS, BusinessInputs, run_planning  # noqa: E402
from app.ui.data_sources import DEMO_DIR, load_demo  # noqa: E402

TEMPLATE = Path(__file__).with_name("demo_app_template.html")
OUTPUT = ROOT / "preview" / "outil_B80.html"
COVERS = [x / 2 for x in range(2, 43)]  # 1 à 21 jours
RUN_DATE = date(2026, 10, 5)

EXAMPLES = BusinessInputs(
    allocations=(
        Allocation(
            boutique="B80",
            sku="7005.70",
            total_qty=4800,
            already_sent=0,
            wave_plan=(75, 25),
            current_wave=1,
            comment="Exemple : promo AOS 14-21 oct",
        ),
    ),  # fmt: skip
    launches=(
        Launch(
            boutique="B80",
            sku="7200.70",
            launch_date=date(2026, 10, 14),
            qty=3600,
            comment="Exemple : lancement Festive",
        ),
    ),  # fmt: skip
    targets=(TargetStock(boutique="B80", sku="142172", min_qty=20, target_qty=30, max_qty=60),),
)
EXAMPLE_TEXT = [
    "Allocation 7005.70 Melozio : 4 800 en 2 vagues 75/25, vague 1 → 3 600",
    "Lancement 7200.70 Hazelnut Brownie le 2026-10-14 : 3 600 en boutique",
    "Stock cible 142172 Travel mug : min 20 / cible 30 / max 60",
]


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def build() -> Path:
    data = load_demo()
    masters = data.masters
    inputs, dc = build_inputs_for_boutique(
        "B80", data.stock, data.movements, masters.products, masters.conversions,
        masters.dc_mapping, sources=data.source_hashes,
    )  # fmt: skip
    portfolio = masters.portfolio.get("B80", {})
    golden = pd.read_csv(DEMO_DIR / "golden_stock_cover_final_B80.csv", dtype=str)
    excel_qty = dict(zip(golden["sku"], golden["excel_suggested_qty"].astype(int), strict=True))
    excel_formula = dict(zip(golden["sku"], golden["excel_cell_is_formula"] == "True", strict=True))

    reasons: list[str] = []
    reason_idx: dict[str, int] = {}

    def rid(text: str) -> int:
        if text not in reason_idx:
            reason_idx[text] = len(reasons)
            reasons.append(text)
        return reason_idx[text]

    skus: list[str] | None = None
    excluded: dict[str, str] = {}
    meta: list[dict] = []
    results: dict[str, list] = {}
    for variant, business in (("none", BusinessInputs()), ("examples", EXAMPLES)):
        for mode in ForecastMode:
            key = f"{mode.value}|{variant}"
            per_line: list[list] = []
            for cover in COVERS:
                params = PlanningParameters(
                    boutique="B80", run_date=RUN_DATE, delivery_date=RUN_DATE,
                    cover_days=cover, coffee_cover_days=None,
                )  # fmt: skip
                if skus is None:
                    first = run_planning(inputs, params, mode, dc=dc, portfolio=portfolio,
                                         business_inputs=EXAMPLES)  # fmt: skip
                    skus, excluded = [x.sku for x in first.lines], first.excluded
                res = run_planning(inputs, params, mode, dc=dc, portfolio=portfolio,
                                   business_inputs=business, skus=skus)  # fmt: skip
                if not per_line:
                    per_line = [[] for _ in res.lines]
                for i, line in enumerate(res.lines):
                    e = line.explanation
                    per_line[i].append(
                        [
                            line.qty,
                            _r(e["raw_need"]),
                            line.status.value[0],
                            [rid(r) for r in line.reasons],
                            1 if line.qty_reliable else 0,
                            _r(e["post_cover_days"]),
                            e["multiple"],
                            e["multiple_source"],
                            line.source.value,
                            line.forecast_qty,
                        ]
                    )
                    if not meta or len(meta) <= i:
                        inp = e["inputs"]
                        fam = family(line)
                        if fam == OTHERS:
                            prod = masters.products.get(line.sku)
                            upp = prod.units_per_pallet if prod else None
                            factor = 1 / upp if upp else None
                        else:
                            factor = 1 / PER_PALLET[fam].get(dc, PER_PALLET[fam]["default"])
                        meta.append(
                            {
                                "sku": line.sku,
                                "desc": line.description or "",
                                "type": line.product_type or "",
                                "coffee": line.product_type == COFFEE_TYPE,
                                "family": fam,
                                "pf": factor,
                                "sales": inp["sales_total"],
                                "sales_own": inp["sales_own"],
                                "sales_old": inp["sales_from_old_skus"],
                                "exp": inp["expected_total"],
                                "exp_own": inp["expected_own"],
                                "exp_old": inp["expected_from_old_skus"],
                                "dc": inp["dc_available"],
                                "cur": _r(e["current_cover_days"]),
                                "conv": bool(inp["sales_from_old_skus"])
                                or "old_sku_blocked" in e["rules_triggered"]
                                or any("onversion" in r for r in line.reasons),
                                "xl": excel_qty.get(line.sku),
                                "xlf": excel_formula.get(line.sku),
                            }
                        )
            results[key] = per_line

    fresh = ValidationReport(source="Fraîcheur")
    check_freshness(fresh, RUN_DATE, movements=[m for m in data.movements if m.location == "B80"])
    quality = [row for rep in [*data.reports, fresh] for row in rep.as_rows()]
    descr = {x.sku: x.description or "" for x in inputs.boutique_stock.values()}

    payload = {
        "meta": {
            "boutique": "B80",
            "dc": dc,
            "run_date": RUN_DATE.isoformat(),
            "history_days": inputs.sales.history_days,
            "rule_versions": RULE_VERSIONS,
            "covers": COVERS,
            "examples": EXAMPLE_TEXT,
            "codes": sorted({m.movement_code for m in data.movements if m.location == "B80"}),
        },
        "reasons": reasons,
        "lines": meta,
        "res": results,
        "excluded": [[s, descr.get(s, ""), r] for s, r in sorted(excluded.items())],
        "quality": quality,
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    out = build()
    print(f"Outil web écrit : {out} ({out.stat().st_size / 1024:.0f} Ko)")
