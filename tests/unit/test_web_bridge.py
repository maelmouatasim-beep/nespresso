"""Version web : le moteur appelé depuis le navigateur donne les mêmes quantités."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.domain.models import ForecastMode, PlanningParameters
from app.ingestion.masters import build_inputs_for_boutique
from app.services.planning import BusinessInputs, run_planning
from app.web import bridge

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "b80_2026-10-05"


@pytest.fixture(scope="module")
def day(tmp_path_factory):  # noqa: ANN201
    import os

    from app.ui.workspace import Workspace, demo_workspace

    old = os.environ.get("COPILOT_DATA_DIR")
    os.environ["COPILOT_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    try:
        ws: Workspace = demo_workspace()
        data = ws.load_day()
    finally:
        if old is None:
            os.environ.pop("COPILOT_DATA_DIR", None)
        else:
            os.environ["COPILOT_DATA_DIR"] = old
    bridge.init(json.dumps(bridge.masters_payload(data.masters, data.exclusions, BusinessInputs())))
    for name in ("stock_situation_B80_CY1.csv", "stock_movements_B80.csv"):
        out = json.loads(bridge.ingest_csv((FIXTURES / name).read_text(encoding="utf-8"), name))
        assert out["kind"] and not out["errors"]
    return data


def plan(**kw) -> dict:  # noqa: ANN003
    params = {"boutique": "B80", "run_date": "2026-10-05", "delivery": "2026-10-08", "cover": 9,
              "coffee": None, "mode": "standard", "examples": False} | kw  # fmt: skip
    return json.loads(bridge.plan(json.dumps(params)))


@pytest.mark.parametrize("mode", [ForecastMode.STANDARD, ForecastMode.EXCEL_PARITY])
def test_browser_engine_gives_the_same_quantities(day, mode: ForecastMode) -> None:  # noqa: ANN001
    out = plan(mode=mode.value)
    inputs, dc = build_inputs_for_boutique(
        "B80", day.stock, day.movements, day.masters.products, day.masters.conversions,
        day.masters.dc_mapping, portfolio=day.masters.portfolio["B80"], exclusions=day.exclusions,
    )  # fmt: skip
    params = PlanningParameters(boutique="B80", run_date=date(2026, 10, 5),
                                delivery_date=date(2026, 10, 8), cover_days=9,
                                coffee_cover_days=None)  # fmt: skip
    res = run_planning(inputs, params, mode, dc=dc, business_inputs=BusinessInputs())
    web = {m["sku"]: t[0] for m, t in zip(out["lines"], out["res"], strict=True)}
    assert web == {x.sku: x.qty for x in res.lines}
    assert web["7005.70"] == 3120 if mode is ForecastMode.STANDARD else True
    assert out["dc"] == "CY1" and out["history_days"] == 7


def test_status_lists_all_boutiques_and_quality(day) -> None:  # noqa: ANN001
    st = json.loads(bridge.status("2026-10-05"))
    assert st["dq"]["moves"] == 1551 and not st["errors"]
    assert ["B80", "BIM Chinook"] in st["boutiques"] and len(st["boutiques"]) >= 38
    assert st["planned"]


def test_unknown_boutique_and_unknown_file_are_explained(day) -> None:  # noqa: ANN001
    assert "Aucune ligne de Stock Situation pour la boutique B81" in plan(boutique="B81")["error"]
    out = json.loads(bridge.ingest(json.dumps([["A", "B"], [1, 2]]), "autre.xlsx"))
    assert out["kind"] is None and "non reconnues" in out["error"]


def test_excel_rows_from_the_page_with_negative_exits(day) -> None:  # noqa: ANN001
    rows = [
        ["Stock Movement Id", "Stock", "Product Nr", "Product Type (Prod)", "Stock Mvt Date",
         "Mvt Code", "Quantity (Sum)", "Mvt Code Descr (Mvt Cd)"],
        ["1", "B80", "7005.70", "C", "2026-10-06", "2801", -20, "CA-LAST CHANCE BOGO"],
        [None],
        ["Applied filters:\nQuantity (Sum) is less than or equal to 0"],
    ]  # fmt: skip
    out = json.loads(bridge.ingest(json.dumps(rows), "data - 2026-10-07T084848.694.xlsx"))
    assert out == {"kind": "stock_movements", "rows": 1, "errors": False, "day": "2026-10-07"}
    # remet les mouvements de test pour ne pas gêner les autres tests
    name = "stock_movements_B80.csv"
    bridge.ingest_csv((FIXTURES / name).read_text(encoding="utf-8"), name)
