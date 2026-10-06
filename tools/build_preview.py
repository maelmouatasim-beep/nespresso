"""Génère un aperçu HTML autonome de la commande B80 (fixture de test).

Toutes les quantités sont calculées ICI, par le moteur Python, pour une grille de
jours de couverture et pour les deux modes. La page HTML ne calcule aucune quantité :
elle affiche le résultat pré-calculé qui correspond aux paramètres choisis.

Usage :
    python tools/build_preview.py            # écrit preview/apercu_B80.html
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models import COFFEE_TYPE, ForecastMode, PlanningParameters  # noqa: E402
from app.engines.forecast import RULE_VERSION, compute_recommendations  # noqa: E402
from app.ingestion.fixtures_loader import build_forecast_inputs, load_fixture_dir  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "b80_2026-10-05"
TEMPLATE = Path(__file__).with_name("preview_template.html")
OUTPUT = ROOT / "preview" / "apercu_B80.html"
COVERS = [x / 2 for x in range(2, 43)]  # 1 à 21 jours, pas de 0,5


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def build() -> Path:
    data = load_fixture_dir(FIXTURE)
    p = data.parameters
    inputs = build_forecast_inputs(data)
    golden = pd.read_csv(FIXTURE / "golden_stock_cover_final_B80.csv", dtype=str)
    skus = list(golden["sku"])

    reasons: list[str] = []
    reason_idx: dict[str, int] = {}

    def rid(text: str) -> int:
        if text not in reason_idx:
            reason_idx[text] = len(reasons)
            reasons.append(text)
        return reason_idx[text]

    results: dict[str, list[list[list]]] = {}
    lines_meta: list[dict] = []
    for mode in ForecastMode:
        per_line: list[list[list]] = [[] for _ in skus]
        for cover in COVERS:
            params = PlanningParameters(
                boutique=p["boutique"],
                run_date=date.fromisoformat(p["run_date"]),
                cover_days=cover,
                coffee_cover_days=None,
            )
            for i, line in enumerate(compute_recommendations(skus, inputs, params, mode)):
                e = line.explanation
                per_line[i].append(
                    [
                        line.qty,
                        _r(e["raw_need"]),
                        line.status.value[0],  # O / R / B
                        [rid(r) for r in line.reasons],
                        1 if line.qty_reliable else 0,
                        _r(e["post_cover_days"]),
                        e["multiple"],
                        e["multiple_source"],
                    ]
                )
                if mode is ForecastMode.EXCEL_PARITY and cover == COVERS[0]:
                    inp = e["inputs"]
                    g = golden.iloc[i]
                    lines_meta.append(
                        {
                            "sku": line.sku,
                            "desc": line.description or "",
                            "type": line.product_type or "",
                            "coffee": line.product_type == COFFEE_TYPE,
                            "sales": inp["sales_total"],
                            "sales_own": inp["sales_own"],
                            "sales_old": inp["sales_from_old_skus"],
                            "exp": inp["expected_total"],
                            "exp_own": inp["expected_own"],
                            "exp_old": inp["expected_from_old_skus"],
                            "dc": inp["dc_available"],
                            "cur": _r(e["current_cover_days"]),
                            "rules": [r for r in e["rules_triggered"] if r.startswith("conv")],
                            "xl_qty": int(g["excel_suggested_qty"]),
                            "xl_formula": g["excel_cell_is_formula"] == "True",
                        }
                    )
        results[mode.value] = per_line

    payload = {
        "meta": {
            "boutique": p["boutique"],
            "dc": p["dc"],
            "run_date": p["run_date"],
            "history_days": inputs.sales.history_days,
            "rule_version": RULE_VERSION,
            "covers": COVERS,
            "excel_cover": p["cover_days"],
        },
        "reasons": reasons,
        "lines": lines_meta,
        "res": results,
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    out = build()
    print(f"Aperçu écrit : {out} ({out.stat().st_size / 1024:.0f} Ko)")
