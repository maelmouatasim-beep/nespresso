"""Référentiels modifiables dans l'outil : relus comme à l'import avant d'être écrits."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.ingestion.masters import load_masters_dir
from app.ingestion.referential_edit import DEFAULT_COLUMNS, check_table, table_csv
from app.ui.workspace import DEMO_MASTER_FILES, DEMO_SOURCE, Workspace


def _ws(tmp_path: Path) -> Workspace:
    ws = Workspace(tmp_path)
    ws.masters_dir.mkdir(parents=True)
    for name in DEMO_MASTER_FILES.values():
        shutil.copy(DEMO_SOURCE / name, ws.masters_dir / name)
    return ws


def test_table_csv_keeps_sku_text_and_drops_empty_rows() -> None:
    text = table_csv(["boutique", "dc"], [["B80", "CY1"], [None, float("nan")], ["", " "]])
    assert text == "boutique,dc\nB80,CY1\n"
    text = table_csv(["sku"], [["0005452"], ["7005.70"]])
    assert "0005452" in text and "7005.70" in text


def test_multiple_change_is_saved_and_used(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = ws.masters_dir / "master_multiples.csv"
    text = path.read_text(encoding="utf-8").replace("134502,,,12,", "134502,,,24,", 1)
    errors, _ = ws.save_table("multiples", text, "Amine")
    assert errors == []
    assert load_masters_dir(ws.masters_dir).products["134502"].order_multiple == 24
    assert ws.meta()["multiples"]["updated_by"] == "Amine"


def test_invalid_table_is_refused_and_file_untouched(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = ws.masters_dir / "master_sku_conversions.csv"
    before = path.read_text(encoding="utf-8")
    bad = table_csv(DEFAULT_COLUMNS["conversions"], [["7882.70", "7965.70", "pas une date", ""]])
    errors, _ = ws.save_table("conversions", bad, "Amine")
    assert errors and path.read_text(encoding="utf-8") == before


def test_multiple_not_a_number_is_refused() -> None:
    bad = table_csv(DEFAULT_COLUMNS["multiples"], [["7005.70", "VER-Melozio", "C", "abc", ""]])
    errors, _ = check_table("multiples", bad)
    assert errors and "abc" in errors[0]


def test_empty_cell_in_dc_mapping_is_refused() -> None:
    errors, _ = check_table("dc_mapping", table_csv(["boutique", "dc"], [["B80", ""]]))
    assert errors


def test_business_table_created_from_scratch(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    text = table_csv(
        DEFAULT_COLUMNS["launches"], [["B80", "7200.70", "2026-10-14", "3600", "Festive"]]
    )
    errors, _ = ws.save_table("launches", text, "Amine")
    assert errors == []
    business, reports = ws.load_business()
    assert business.launches[0].sku == "7200.70" and business.launches[0].qty == 3600
    assert not any(r.has_errors for r in reports)


def test_business_error_is_reported_with_its_line() -> None:
    bad = table_csv(DEFAULT_COLUMNS["launches"], [["B80", "7200.70", "2026-10-14", "beaucoup", ""]])
    errors, _ = check_table("launches", bad)
    assert errors


def test_schedule_unreadable_day_is_a_warning_not_a_block(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    rows = [["B80", "", "", "", "Lundi?", "", "Mercredi", "", ""]]
    errors, warnings = ws.save_table("schedule", table_csv(DEFAULT_COLUMNS["schedule"], rows), "A")
    assert errors == [] and warnings


def test_portfolio_file_is_replaced_in_place(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    text = table_csv(DEFAULT_COLUMNS["portfolio"], [["B80", "7005.70", "Yes"]])
    errors, _ = ws.save_table("portfolio", text, "A", "master_boutique_portfolio_B80.csv")
    assert errors == []
    assert load_masters_dir(ws.masters_dir).portfolio == {"B80": {"7005.70": "Yes"}}
