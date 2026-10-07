"""Tests de bout en bout de l'interface Streamlit (sans navigateur)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[2] / "app" / "ui" / "main.py")
PAGES = ("1. Dépôt du jour", "2. Commande boutique", "3. Journée", "4. Export",
         "5. Référentiels", "6. Historique")  # fmt: skip


@pytest.fixture
def app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("COPILOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COPILOT_DB", str(tmp_path / "test.db"))
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    at.radio(key="ws_kind").set_value("Démo B80").run()
    return at


def goto(at: AppTest, page: str) -> None:
    at.radio(key="page").set_value(page).run()
    assert not at.exception, at.exception


def compute_b80(at: AppTest, mode: str = "Standard") -> None:
    goto(at, PAGES[1])
    at.selectbox(key="p_boutique").set_value("B80").run()
    at.date_input(key="p_delivery").set_value(date(2026, 10, 8)).run()
    at.number_input(key="p_cover").set_value(9.0).run()
    at.selectbox(key="p_mode").set_value(mode).run()
    at.button(key="compute").click().run()
    assert not at.exception, at.exception


def test_every_screen_renders(app: AppTest) -> None:
    for page in PAGES:
        goto(app, page)
    goto(app, PAGES[0])
    assert any(b.key == "ready" for b in app.button)


def test_order_requires_parameters_then_computes(app: AppTest) -> None:
    goto(app, PAGES[1])
    app.button(key="compute").click().run()
    assert any("Il manque" in e.value for e in app.error)
    compute_b80(app)
    res = app.session_state["base"]
    by_sku = {x.sku: x for x in res.lines}
    assert by_sku["7005.70"].qty == 3120
    assert any("dc_return" in x.explanation["rules_triggered"] for x in res.lines)
    assert res.history_days == 7


def test_parity_mode_reproduces_excel_selection(app: AppTest) -> None:
    compute_b80(app, mode="Parité Excel")
    res = app.session_state["base"]
    assert len(res.lines) == 452  # 451 lignes Excel + 2007.70 (question ouverte)


def test_export_needs_planner_name(app: AppTest) -> None:
    compute_b80(app)
    goto(app, PAGES[3])
    assert any("Indique ton nom" in e.value for e in app.error)
    app.text_input(key="author").input("Planner Test").run()
    assert not app.exception
    app.button(key="save_run").click().run()
    assert app.session_state["saved_run_id"] == 1


def test_day_requires_confirmed_date_and_cover(app: AppTest) -> None:
    goto(app, PAGES[2])
    pick = next(m for m in app.multiselect if m.key.startswith("day_btq_"))
    pick.set_value(["B80"]).run()
    app.button(key="day_compute").click().run()
    assert any("date de livraison" in e.value for e in app.error)


def test_referential_owner_is_saved(app: AppTest) -> None:
    goto(app, PAGES[4])
    app.text_input(key="owner_multiples").input("Équipe planning MTL").run()
    assert not app.exception
    from app.ui.workspace import demo_workspace

    assert demo_workspace().meta()["multiples"]["owner"] == "Équipe planning MTL"


def grid(at: AppTest):
    return next(d for d in at.dataframe if d.key and d.key.startswith("grid_")).value


def test_single_grid_default_order_and_filters(app: AppTest) -> None:
    compute_b80(app)
    df = grid(app)
    assert len(df) == len(app.session_state["base"].lines)  # une seule grille, toutes les lignes
    flagged = ~df["Statut"].str.contains("OK")
    first_ok = int(flagged.values.argmin())
    assert flagged[:first_ok].all() and not flagged[first_ok:].any()  # REVIEW/BLOCKED en haut
    ok = df[first_ok:]["Qty proposée"].tolist()
    assert ok == sorted(ok, reverse=True)  # puis Qty proposée décroissante
    assert list(df.columns[1:5]) == ["Statut", "SKU", "Description", "Catégorie"]

    next(b for b in app.button_group if b.key == "f_cat").set_value("Cafés").run()
    assert set(grid(app)["Catégorie"]) == {"Cafés"}
    next(b for b in app.button_group if b.key == "f_status").set_value("REVIEW").run()
    assert grid(app)["Statut"].str.contains("REVIEW").all()
    app.checkbox(key="f_qty").check().run()
    assert (grid(app)["Qty finale"] > 0).all()


def test_accept_all_ok_marks_ok_lines(app: AppTest) -> None:
    compute_b80(app)
    app.button(key="accept_ok").click().run()
    df = grid(app)
    assert df[df["Statut"].str.contains("OK")]["Statut"].str.endswith("✓").all()
    assert not df[df["Statut"].str.contains("REVIEW")]["Statut"].str.endswith("✓").any()
