"""Onglets Produits et Boutiques : une ligne par SKU / par boutique, fichiers sources réécrits
seulement là où le planner a modifié quelque chose."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from app.ingestion.boutique_table import CARRIER, CODE, DC, NAME, PLANNER
from app.ingestion.masters import load_masters_dir
from app.ingestion.product_table import (
    ANOMALIES,
    CAT,
    CONV_DATE,
    EXCL,
    EXCL_REASON,
    MULT,
    OLD,
    SKU,
    build_product_rows,
)
from app.ui.workspace import DEMO_MASTER_FILES, DEMO_SOURCE, Workspace


def _ws(tmp_path: Path) -> Workspace:
    ws = Workspace(tmp_path)
    ws.masters_dir.mkdir(parents=True)
    for name in DEMO_MASTER_FILES.values():
        shutil.copy(DEMO_SOURCE / name, ws.masters_dir / name)
    ws.masters_dir.joinpath("exclusions.csv").write_text(
        "sku,boutiques,reason,author,updated_on\n7010.70,ALL,SKU e-commerce,Équipe,2026-10-07\n",
        encoding="utf-8",
    )
    return ws


def _rows(ws: Workspace) -> dict[str, dict[str, str]]:
    return {r[SKU]: r for r in build_product_rows(ws.product_sources())}


def test_one_row_per_sku_with_anomalies(tmp_path: Path) -> None:
    rows = _rows(_ws(tmp_path))
    assert "doublon (3 lignes)" in rows["7005.70"][ANOMALIES]
    assert rows["7005.70"][CAT] == "Cafés" and rows["7005.70"][MULT] == "240"
    assert rows["7965.70"][OLD] == "7882.70" and rows["7965.70"][CONV_DATE] == "2026-09-02"
    assert "7990.70" in rows["XYZ123"][OLD]
    assert rows["7010.70"][EXCL] == "oui"
    assert any("espace parasite" in r[ANOMALIES] for r in rows.values())


def test_unchanged_table_rewrites_nothing(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    before = (ws.masters_dir / "master_multiples.csv").read_bytes()
    errors, _ = ws.save_products(list(_rows(ws).values()), "Amine")
    assert errors == []
    assert (ws.masters_dir / "master_multiples.csv").read_bytes() == before
    assert ws.history("produits") == []


def test_multiple_change_applies_to_every_duplicate_line(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    rows = _rows(ws)
    rows["7005.70"][MULT] = "480"
    errors, _ = ws.save_products(list(rows.values()), "Amine")
    assert errors == []
    product = load_masters_dir(ws.masters_dir).products["7005.70"]
    assert product.order_multiple == 480 and product.alt_multiples == ()
    assert "Multiple 240 → 480" in ws.history("produits")[0]["changes"][0]


def test_conversion_exclusion_and_category_are_edited_from_the_same_row(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    rows = _rows(ws)
    rows["7072.70"].update({OLD: "7071.70", CONV_DATE: "2026-10-20", EXCL: "B80",
                            EXCL_REASON: "test boutique"})  # fmt: skip
    rows["+25CC-GOLF2019"][CAT] = "Accessoires"  # type Z : tout le type change de catégorie
    errors, _ = ws.save_products(list(rows.values()), "Amine", date(2026, 10, 12))
    assert errors == []
    m = load_masters_dir(ws.masters_dir)
    assert any(c.old_sku == "7071.70" and c.new_sku == "7072.70" for c in m.conversions)
    excl, _ = ws.load_exclusions()
    assert [(e.sku, e.boutiques) for e in excl] == [("7010.70", ()), ("7072.70", ("B80",))]
    assert ws.load_categories()[0]["Z"] == "Accessoires"


def test_errors_block_the_save(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    rows = _rows(ws)
    rows["7072.70"].update({EXCL: "oui", EXCL_REASON: ""})
    rows["7005.70"][MULT] = "abc"
    before = (ws.masters_dir / "master_multiples.csv").read_bytes()
    errors, _ = ws.save_products(list(rows.values()), "Amine")
    assert errors
    assert (ws.masters_dir / "master_multiples.csv").read_bytes() == before


def test_removed_row_drops_the_sku(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    rows = _rows(ws)
    del rows["7005.70"]
    assert ws.save_products(list(rows.values()), "Amine")[0] == []
    assert "7005.70" not in load_masters_dir(ws.masters_dir).products


def test_boutique_row_edits_dc_name_carrier_planner(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    rows = {r[CODE]: r for r in ws.boutique_rows()}
    assert rows["B80"][DC] == "CY1"
    rows["B80"].update({NAME: "BIM Chinook (test)", PLANNER: "Sami", CARRIER: "Transport X"})
    rows["B37"][DC] = "MT1"
    assert ws.save_boutiques(list(rows.values()), "Amine") == []
    m = load_masters_dir(ws.masters_dir)
    assert m.dc_mapping["B37"] == "MT1" and m.boutique_names["B80"] == "BIM Chinook (test)"
    assert {s.carrier for s in m.schedule if s.boutique == "B80"} == {"Transport X"}
    again = {r[CODE]: r for r in ws.boutique_rows()}
    assert again["B80"][PLANNER] == "Sami"


def test_slots_rules_and_portfolio_of_one_boutique(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _, slots = ws.boutique_part("slots", "B80")
    assert slots
    errors, _ = ws.save_boutique_part("slots", "B80", [{"order_day": "Monday",
                                      "delivery_day": "Wednesday"}], "Amine")  # fmt: skip
    assert errors == []
    m = load_masters_dir(ws.masters_dir)
    assert len([s for s in m.schedule if s.boutique == "B80"]) == 1
    assert m.boutique_names["B80"]
    rules = [{"rule": "max_pallets", "sku": "", "value": "3", "comment": ""}]
    assert ws.save_boutique_part("rules", "B80", rules, "Amine")[0] == []
    assert ws.load_business()[0].rules[0].boutique == "B80"
    pf = [{"sku": "7005.70", "filter_out": "Yes"}]
    assert ws.save_boutique_part("portfolio", "B80", pf, "Amine")[0] == []
    assert load_masters_dir(ws.masters_dir).portfolio["B80"] == {"7005.70": "Yes"}
