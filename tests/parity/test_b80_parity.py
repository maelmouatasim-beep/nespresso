"""Parité avec le calculateur Excel « B80 105.xlsm » (Stock Cover Final, 05-oct-2026).

Le moteur recalcule les 461 lignes à partir des fichiers BRUTS uniquement. Le
golden ne sert qu'à comparer : aucune colonne de résultat n'est utilisée en entrée.

- 416 lignes calculées par formule : la quantité doit être STRICTEMENT égale.
- 45 overrides manuels du planner : pas d'échec, un rapport est produit dans
  tests/parity/reports/overrides_B80.md.

Tout écart connu est listé ici ET documenté dans docs/decision_log.md. Le test
vérifie aussi qu'un écart connu existe toujours, pour que la liste ne vieillisse pas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.domain.models import ForecastMode, PlanningParameters, RecommendationLine
from app.engines.forecast import compute_recommendations
from app.ingestion.fixtures_loader import build_forecast_inputs, load_fixture_dir

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "b80_2026-10-05"
REPORT_PATH = Path(__file__).resolve().parent / "reports" / "overrides_B80.md"
TOLERANCE = 1e-6

# Écarts d'AFFICHAGE connus : la quantité, elle, est identique. Voir docs/decision_log.md.
#
# D-003 : pour un new SKU, Excel AFFICHE les ventes / l'Expected / les couvertures du
# new SKU seul, alors que notre moteur affiche les totaux old + new (ceux qui servent au
# calcul de la quantité). Seuls les new SKU dont l'old SKU a encore de l'activité sont
# concernés.
KNOWN_CONVERSION_DISPLAY_DIVERGENCES = {"7934.70", "7947.70", "7961.70"}

# D-004 : stock DC (CY1) d'Excel légèrement supérieur à l'export Power BI fourni
# (+1 à +150 unités) : les deux extractions n'ont pas été faites au même moment.
KNOWN_DC_STOCK_DIVERGENCES = {
    "109793", "136054", "139848", "144057", "159671", "160253", "2016.70", "7003.70",
    "7030.70", "7072.70", "7087.70", "7088.70", "7092.70", "7231.70", "7294.70",
    "7295.70", "7296.70", "7298.70", "7984.70", "F121-US-WH-DL",
}  # fmt: skip


@dataclass(frozen=True)
class ParityRun:
    golden: pd.DataFrame
    lines: dict[str, RecommendationLine]
    history_days: int
    expected_history_days: int


@pytest.fixture(scope="module")
def run() -> ParityRun:
    data = load_fixture_dir(FIXTURE_DIR)
    p = data.parameters
    params = PlanningParameters(
        boutique=p["boutique"],
        run_date=date.fromisoformat(p["run_date"]),
        cover_days=p["cover_days"],
        coffee_cover_days=p["coffee_cover_days"],
    )
    inputs = build_forecast_inputs(data)
    golden = pd.read_csv(FIXTURE_DIR / "golden_stock_cover_final_B80.csv", dtype=str)
    golden["is_formula"] = golden["excel_cell_is_formula"].map({"True": True, "False": False})
    assert golden["is_formula"].notna().all()
    lines = compute_recommendations(golden["sku"], inputs, params, ForecastMode.EXCEL_PARITY)
    return ParityRun(
        golden=golden,
        lines={line.sku: line for line in lines},
        history_days=inputs.sales.history_days,
        expected_history_days=p["sales_history_days"],
    )


def _num(value: object) -> float | None:
    return None if pd.isna(value) else float(str(value))


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=0, abs_tol=TOLERANCE)


def _formula_rows(run: ParityRun) -> pd.DataFrame:
    return run.golden[run.golden["is_formula"]]


def test_golden_shape(run: ParityRun) -> None:
    assert len(run.golden) == 461
    assert run.golden["is_formula"].sum() == 416
    assert run.golden["sku"].is_unique


def test_sales_history_days_matches_excel(run: ParityRun) -> None:
    assert run.history_days == run.expected_history_days == 7


def test_qty_parity_on_formula_rows(run: ParityRun) -> None:
    rows = _formula_rows(run)
    mismatches = [
        (r.sku, run.lines[r.sku].qty, int(r.excel_suggested_qty))
        for r in rows.itertuples()
        if run.lines[r.sku].qty != int(r.excel_suggested_qty)
    ]
    matched = len(rows) - len(mismatches)
    print(f"\nParité qty : {matched}/{len(rows)}")
    assert not mismatches, f"Écarts (sku, moteur, excel) : {mismatches}"


def test_cover_days_parity_on_formula_rows(run: ParityRun) -> None:
    bad = []
    for r in _formula_rows(run).itertuples():
        if r.sku in KNOWN_CONVERSION_DISPLAY_DIVERGENCES:
            continue
        exp = run.lines[r.sku].explanation
        for col in ("current_cover_days", "post_cover_days"):
            if not _close(exp[col], _num(getattr(r, col))):
                bad.append((r.sku, col, exp[col], getattr(r, col)))
    assert not bad, bad


def test_sales_and_expected_inputs_match_excel(run: ParityRun) -> None:
    """Les entrées affichées par Excel (ventes, Expected, multiple, stock DC) concordent."""
    bad = []
    for r in _formula_rows(run).itertuples():
        exp = run.lines[r.sku].explanation
        inp = exp["inputs"]
        if r.sku not in KNOWN_CONVERSION_DISPLAY_DIVERGENCES:
            if not _close(inp["sales_total"], _num(r.sales_hist)):
                bad.append((r.sku, "sales", inp["sales_total"], r.sales_hist))
            if not _close(inp["expected_total"], _num(r.expected)):
                bad.append((r.sku, "expected", inp["expected_total"], r.expected))
        # SKU absent du DC : Excel affiche 0, notre moteur garde « inconnu » (None).
        dc = inp["dc_available"] if inp["dc_available"] is not None else 0.0
        if r.sku not in KNOWN_DC_STOCK_DIVERGENCES and not _close(dc, _num(r.dc_stock)):
            bad.append((r.sku, "dc_stock", inp["dc_available"], r.dc_stock))
        golden_multiple = _num(r.multiple)
        if golden_multiple is None:
            # Multiple vide dans Excel : la formule utilise 1.
            if exp["multiple_source"] != "excel_default_1":
                bad.append((r.sku, "multiple", exp["multiple"], "vide"))
        elif exp["multiple"] != golden_multiple:
            bad.append((r.sku, "multiple", exp["multiple"], r.multiple))
    assert not bad, bad


def test_known_conversion_display_divergences(run: ParityRun) -> None:
    """D-003 : Excel affiche les valeurs du new SKU seul ; la quantité reste identique.

    Si l'écart disparaît, retirer l'entrée ET mettre à jour docs/decision_log.md.
    """
    golden = run.golden.set_index("sku")
    for sku in KNOWN_CONVERSION_DISPLAY_DIVERGENCES:
        g = golden.loc[sku]
        line = run.lines[sku]
        inp = line.explanation["inputs"]
        assert not (
            _close(inp["sales_total"], _num(g["sales_hist"]))
            and _close(inp["expected_total"], _num(g["expected"]))
        ), f"{sku} : l'écart a disparu"
        # Excel affiche exactement les valeurs propres du new SKU...
        assert _close(inp["sales_own"], _num(g["sales_hist"])), sku
        assert _close(inp["expected_own"], _num(g["expected"])), sku
        excel_cover = inp["expected_own"] / inp["sales_own"] * run.history_days
        assert _close(excel_cover, _num(g["current_cover_days"])), sku
        # ... et la quantité est la même.
        assert line.qty == int(g["excel_suggested_qty"]), sku


def test_known_dc_stock_divergences(run: ParityRun) -> None:
    """D-004 : écarts de stock DC dus à des extractions faites à des moments différents."""
    golden = run.golden.set_index("sku")
    for sku in KNOWN_DC_STOCK_DIVERGENCES:
        engine_dc = run.lines[sku].explanation["inputs"]["dc_available"]
        diff = _num(golden.loc[sku, "dc_stock"]) - engine_dc
        assert 0 < diff <= 150, (sku, diff)


def test_old_sku_blocked_flags_match_excel(run: ParityRun) -> None:
    golden = run.golden[run.golden["old_sku_still_orderable"].notna()]
    for r in golden.itertuples():
        blocked = "old_sku_blocked" in run.lines[r.sku].explanation["rules_triggered"]
        assert blocked == (r.old_sku_still_orderable == "NO"), r.sku


def test_overrides_report(run: ParityRun) -> None:
    """Les 45 overrides ne font pas échouer : ils sont listés dans un rapport."""
    overrides = run.golden[~run.golden["is_formula"]]
    assert len(overrides) == 45
    rows = []
    for r in overrides.itertuples():
        line = run.lines[r.sku]
        planner = int(r.excel_suggested_qty)
        rows.append(
            f"| {r.sku} | {r.description if isinstance(r.description, str) else ''} "
            f"| {r.product_type if isinstance(r.product_type, str) else ''} "
            f"| {line.qty} | {planner} | {planner - line.qty:+d} | {line.status.value} |"
        )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        "# Overrides manuels du planner — B80, 05-oct-2026\n\n"
        "Fichier généré par `tests/parity/test_b80_parity.py`. Ne pas modifier à la main.\n\n"
        "Lignes où la cellule Suggested Qty d'Excel contient une valeur tapée à la main "
        "au lieu de la formule. « Qty formule » = ce que donne le moteur en mode "
        "`excel_parity`. Les 10 dernières lignes (sans type) sont des SKU ajoutés à la "
        "main sous le tableau : ils sont absents de la Stock Situation.\n\n"
        f"Nombre d'overrides : {len(rows)}\n\n"
        "| SKU | Description | Type | Qty formule | Qty planner | Écart | Statut moteur |\n"
        "|---|---|---|---:|---:|---:|---|\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    melozio = run.lines["7005.70"]
    assert melozio.qty == 3120
    assert int(overrides.set_index("sku").loc["7005.70", "excel_suggested_qty"]) == 3600
