"""Version web : référentiels et événements modifiés dans la page, appliqués par le moteur."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.ingestion.referential_edit import DEFAULT_COLUMNS, table_csv
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
    bridge.init(json.dumps(bridge.masters_payload(data.masters, data.exclusions)))
    for name in ("stock_situation_B80_CY1.csv", "stock_movements_B80.csv"):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        assert json.loads(bridge.ingest_csv(text, name, bridge.DEMO))["kind"]
    return data


def plan(**kw) -> dict:  # noqa: ANN003
    params = {"boutique": "B80", "run_date": "2026-10-05", "delivery": "2026-10-08",
              "cover": 9, "coffee": None, "mode": "standard",
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


def test_edited_exclusion_and_launch_event_are_used(demo) -> None:  # noqa: ANN001
    excl = "sku,boutiques,reason,author,updated_on\n7072.70,ALL,test,Amine,2026-10-12\n"
    out = json.loads(bridge.set_tables(json.dumps({"exclusions": excl})))
    assert all(not v["errors"] for v in out["tables"].values())
    launch = {"id": 9, "scope": "team", "type": "launch", "title": "Festive",
              "skus": [{"sku": "7200.70"}], "boutiques": ["B80"], "start": "2026-10-14",
              "end": "2026-10-14", "lead_days": 9, "effect": "initial_qty",
              "quantities": [{"qty": 3600}]}  # fmt: skip
    checked = json.loads(bridge.check_event(json.dumps(launch)))
    assert checked["errors"] == []
    res = plan(events=[checked["event"]])
    assert qty(res, "7072.70") == 0
    assert qty(res, "7200.70") > 0


def test_old_business_table_from_a_browser_is_refused_with_its_reason(demo) -> None:  # noqa: ANN001
    launch = table_csv(DEFAULT_COLUMNS["launches"], [["B80", "7200.70", "2026-10-14", "3600", ""]])
    out = json.loads(bridge.set_tables(json.dumps({"launches": launch})))
    assert "Événements SKU" in out["tables"]["launches"]["errors"][0]


def test_event_checked_with_the_same_rules() -> None:
    ok = {"scope": "team", "type": "ban", "title": "Test", "skus": [{"sku": "7005.70"}],
          "start": "2026-10-05", "effect": "block"}  # fmt: skip
    assert json.loads(bridge.check_event(json.dumps(ok)))["errors"] == []
    bad = ok | {"effect": "set_qty"}
    assert json.loads(bridge.check_event(json.dumps(bad)))["errors"]


def _fixture_table(name: str) -> dict:
    from app.ingestion.fixtures_loader import read_csv_records

    cols, recs = read_csv_records(FIXTURES / name)
    return {"columns": cols, "rows": [[r[c] for c in cols] for r in recs]}


def test_products_tab_in_the_page_changes_the_order(demo) -> None:  # noqa: ANN001
    tables = {"multiples": _fixture_table("master_multiples.csv"),
              "conversions": _fixture_table("master_sku_conversions.csv"),
              "exclusions": {"columns": ["sku", "boutiques", "reason", "author", "updated_on"],
                             "rows": []}}  # fmt: skip
    payload = {"tables": tables, "categories": {"C": "Cafés"}}
    rows = json.loads(bridge.product_rows(json.dumps(payload)))
    row = next(r for r in rows if r["SKU"] == "7072.70")
    row.update({"Exclu": "oui", "Raison exclusion": "test web"})
    out = json.loads(bridge.save_products(json.dumps(
        payload | {"edited": rows, "author": "Amine", "today": "2026-10-12"})))  # fmt: skip
    assert out["errors"] == [] and set(out["tables"]) == {"exclusions"}
    assert qty(plan(), "7072.70") == 0


def test_boutiques_tab_and_part_in_the_page(demo) -> None:  # noqa: ANN001
    tables = {"dc_mapping": _fixture_table("master_dc_mapping.csv"),
              "schedule": _fixture_table("master_schedule.csv")}  # fmt: skip
    rows = json.loads(bridge.boutique_rows(json.dumps({"tables": tables})))
    next(r for r in rows if r["Code"] == "B80")["Nom"] = "Chinook test"
    out = json.loads(bridge.save_boutiques(json.dumps({"tables": tables, "edited": rows})))
    assert out["errors"] == [] and out["tables"]["boutiques"]["rows"][0][1] == "Chinook test"
    rules = {"part": "rules", "boutique": "B80", "table": None,
             "rows": [{"rule": "max_units", "sku": "", "value": "10", "comment": ""}]}  # fmt: skip
    res = json.loads(bridge.save_part(json.dumps(rules)))
    assert res["errors"] == []
    assert any("10" in w for w in plan()["warnings"])
