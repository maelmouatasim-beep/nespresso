"""Version web : référentiels et événements modifiés dans la page, appliqués par le moteur."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.ingestion.referential_edit import DEFAULT_COLUMNS, table_csv
from app.services.planning import BusinessInputs
from app.web import bridge

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "b80_2026-10-05"


@pytest.fixture
def demo(tmp_path):  # noqa: ANN001, ANN201
    from app.ui.workspace import demo_workspace

    old = os.environ.get("COPILOT_DATA_DIR")
    os.environ["COPILOT_DATA_DIR"] = str(tmp_path)
    try:
        data = demo_workspace().load_day()
    finally:
        if old is None:
            os.environ.pop("COPILOT_DATA_DIR", None)
        else:
            os.environ["COPILOT_DATA_DIR"] = old
    bridge.init(json.dumps(bridge.masters_payload(data.masters, data.exclusions, BusinessInputs())))
    for name in ("stock_situation_B80_CY1.csv", "stock_movements_B80.csv"):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        assert json.loads(bridge.ingest_csv(text, name, bridge.DEMO))["kind"]
    return data


def plan(**kw) -> dict:  # noqa: ANN003
    params = {"boutique": "B80", "run_date": "2026-10-05", "delivery": "2026-10-08",
              "cover": 9, "coffee": None, "mode": "standard", "examples": True,
              "dataset": "demo"} | kw  # fmt: skip
    return json.loads(bridge.plan(json.dumps(params)))


def qty(out: dict, sku: str) -> int:
    return next(t[0] for m, t in zip(out["lines"], out["res"], strict=True) if m["sku"] == sku)


def test_demo_dataset_is_separate_from_my_files(demo) -> None:  # noqa: ANN001
    assert qty(plan(), "7005.70") == 3120
    assert bridge._data(bridge.DEMO) is not bridge._data(bridge.LIVE)


def test_edited_multiple_changes_the_order(demo) -> None:  # noqa: ANN001
    text = (FIXTURES / "master_multiples.csv").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("7005.70,"))
    parts = line.split(",")
    parts[3] = "1000"
    out = json.loads(
        bridge.set_tables(json.dumps({"multiples": text.replace(line, ",".join(parts))}))
    )
    assert out["tables"]["multiples"]["errors"] == []
    assert qty(plan(), "7005.70") % 1000 == 0


def test_invalid_table_is_not_applied(demo) -> None:  # noqa: ANN001
    bad = table_csv(DEFAULT_COLUMNS["dc_mapping"], [["B80", ""]])
    out = json.loads(bridge.set_tables(json.dumps({"dc_mapping": bad})))
    assert out["tables"]["dc_mapping"]["errors"]
    assert plan()["dc"] == "CY1"


def test_edited_exclusion_and_launch_are_used(demo) -> None:  # noqa: ANN001
    excl = "sku,boutiques,reason,author,updated_on\n7072.70,ALL,test,Amine,2026-10-12\n"
    launch = table_csv(DEFAULT_COLUMNS["launches"], [["B80", "7200.70", "2026-10-14", "3600", ""]])
    out = json.loads(bridge.set_tables(json.dumps({"exclusions": excl, "launches": launch})))
    assert all(not v["errors"] for v in out["tables"].values())
    res = plan()
    assert qty(res, "7072.70") == 0
    assert qty(res, "7200.70") > 0


def test_event_checked_with_the_same_rules() -> None:
    ok = {"scope": "team", "type": "ban", "title": "Test", "skus": [{"sku": "7005.70"}],
          "start": "2026-10-05", "effect": "block"}  # fmt: skip
    assert json.loads(bridge.check_event(json.dumps(ok)))["errors"] == []
    bad = ok | {"effect": "set_qty"}
    assert json.loads(bridge.check_event(json.dumps(bad)))["errors"]
