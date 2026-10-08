"""Catégories d'affichage (filtre « Catégorie ») : mapping du planner, référentiel modifiable."""

from __future__ import annotations

import io

import pytest

from app.engines.categories import (
    ACCESSORIES,
    CAFES,
    CONSUMABLES,
    DEFAULT_CATEGORY_MAP,
    MACHINES,
    OTHERS,
    category_of,
)
from app.ingestion.categories import categories_csv, check_mapping, read_categories


@pytest.mark.parametrize(
    ("ptype", "expected"),
    [
        ("C", CAFES),
        ("M", MACHINES),
        *[(t, CONSUMABLES) for t in ("A", "AP", "D", "FB", "PA", "PM", "MD")],
        *[(t, ACCESSORIES) for t in ("LC", "CH", "GC", "T", "TX", "F")],
        ("P", OTHERS),
        ("", OTHERS),
        (None, OTHERS),
    ],
)
def test_default_mapping_is_the_planner_one(ptype: str | None, expected: str) -> None:
    assert category_of(ptype) == expected


def test_type_is_normalized() -> None:
    assert category_of(" lc ") == ACCESSORIES


def test_custom_mapping_wins_and_unknown_type_goes_to_others() -> None:
    mapping = {"P": ACCESSORIES}
    assert category_of("P", mapping) == ACCESSORIES
    assert category_of("C", mapping) == OTHERS


def test_check_mapping_rejects_unknown_category_and_conflicts() -> None:
    mapping, errors = check_mapping([("c", CAFES), ("X", "Bonbons"), ("C", MACHINES), ("", CAFES)])
    assert mapping == {"C": CAFES}
    assert len(errors) == 3
    assert any("Bonbons" in e for e in errors)


def test_csv_round_trip() -> None:
    text = categories_csv(DEFAULT_CATEGORY_MAP)
    mapping, report = read_categories(io.BytesIO(text.encode("utf-8")), "categories.csv")
    assert not report.has_errors
    assert mapping == dict(DEFAULT_CATEGORY_MAP)


def test_invalid_file_is_reported_not_silently_used() -> None:
    text = "product_type,categorie\nC,Cafés\nM,Robots\n"
    mapping, report = read_categories(io.BytesIO(text.encode("utf-8")), "categories.csv")
    assert mapping == {}
    assert report.has_errors


def test_workspace_falls_back_to_default_with_a_warning(tmp_path) -> None:
    from app.ui.workspace import Workspace

    ws = Workspace(tmp_path)
    mapping, report = ws.load_categories()
    assert mapping == dict(DEFAULT_CATEGORY_MAP) and not report.issues
    ws.save_categories({"C": CAFES, "P": ACCESSORIES}, "Planner")
    assert ws.load_categories()[0] == {"C": CAFES, "P": ACCESSORIES}
    assert ws.meta()["categories"]["updated_by"] == "Planner"
    (ws.masters_dir / "categories.csv").write_text("product_type,categorie\nC,Robots\n", "utf-8")
    mapping, report = ws.load_categories()
    assert mapping == dict(DEFAULT_CATEGORY_MAP)
    assert [i.level for i in report.issues] == ["warning"]
