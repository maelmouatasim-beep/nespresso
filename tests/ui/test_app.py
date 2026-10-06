"""Test de bout en bout de l'interface Streamlit (sans navigateur)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[2] / "app" / "ui" / "main.py")


@pytest.fixture
def app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("COPILOT_DB", str(tmp_path / "test.db"))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def test_demo_flow(app: AppTest) -> None:
    assert not app.exception
    app.button(key="load_demo").click().run()
    assert not app.exception
    # Paramètres obligatoires manquants : message clair, pas de calcul.
    app.button(key="compute").click().run()
    assert any("Il manque" in e.value for e in app.error)

    app.text_input(key="author").input("Planner Test").run()
    app.date_input(key="delivery_date").set_value(date(2026, 10, 7)).run()
    app.number_input(key="cover").set_value(9.0).run()
    app.radio(key="mode").set_value("Parité Excel").run()
    app.button(key="compute").click().run()
    assert not app.exception
    assert any("Commande calculée" in s.value for s in app.success)
    res = app.session_state["base"]
    melozio = next(x for x in res.lines if x.sku == "7005.70")
    assert melozio.qty == 3120

    # Enregistrement du run dans la base de test.
    app.button(key="save").click().run()
    assert not app.exception
    assert app.session_state["saved_run_id"] == 1


def test_search_then_override_through_engine(app: AppTest) -> None:
    app.button(key="load_demo").click().run()
    app.text_input(key="author").input("Planner Test").run()
    app.date_input(key="delivery_date").set_value(date(2026, 10, 7)).run()
    app.number_input(key="cover").set_value(9.0).run()
    app.button(key="compute").click().run()
    app.text_input(key="f_search").input("Melozio").run()
    assert not app.exception
    # L'override passe par le moteur : on vérifie la règle via la session.
    from datetime import datetime

    from app.domain.models import Override
    from app.services.planning import with_overrides

    base = app.session_state["base"]
    ov = Override(sku="7005.70", qty_before=3120, qty_after=3600, reason="Promo AOS",
                  author="Planner Test", timestamp=datetime.now())  # fmt: skip
    final = with_overrides(base, [ov], {})
    assert next(x for x in final.lines if x.sku == "7005.70").qty == 3600
