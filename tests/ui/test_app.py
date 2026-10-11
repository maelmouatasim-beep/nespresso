"""Tests de bout en bout de l'interface Streamlit (sans navigateur)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[2] / "app" / "ui" / "main.py")
PAGES = ("1. Dépôt du jour", "2. Commande boutique", "3. Journée", "4. Export",
         "5. Événements SKU", "6. Référentiels", "7. Historique")  # fmt: skip
EVENTS, REFS = PAGES[4], PAGES[5]


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
    # 451 lignes Excel + 2007.70 (question ouverte) + ADV-2026-CA, lancement fictif de la
    # démo absent de la Stock Situation (un événement n'oublie jamais un SKU).
    assert len(res.lines) == 453
    assert "ADV-2026-CA" in {x.sku for x in res.lines}


def test_export_needs_planner_name(app: AppTest) -> None:
    compute_b80(app)
    goto(app, PAGES[3])
    assert any("Choisis ton prénom" in e.value for e in app.error)
    app.selectbox(key="author").set_value("Sami").run()
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
    goto(app, REFS)
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


def test_my_data_depot_offers_referentials_and_uploader(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COPILOT_DB", str(tmp_path / "test.db"))
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()  # « Mes données » par défaut, rien de déposé
    assert not at.exception
    assert any("Il manque les référentiels" in w.value for w in at.warning)
    at.text_input(key="new_planner").input("Planner Test").run()
    at.button(key="add_planner").click().run()
    assert at.session_state["author"] == "Planner Test"
    at.button(key="test_refs_depot").click().run()
    assert not at.exception
    from app.ui.workspace import real_workspace

    assert real_workspace().has_masters()


def test_demo_depot_upload_is_disabled(app: AppTest) -> None:
    assert any("Démo B80" in i.value for i in app.info)


# --- Événements SKU ------------------------------------------------------------------------


def by_suffix(elements, suffix: str):  # noqa: ANN001, ANN201
    return next(e for e in elements if e.key and e.key.endswith(suffix))


def ev_grid(at: AppTest):
    return next(d for d in at.dataframe if d.key and d.key.startswith("ev_grid_")).value


def test_events_page_two_tabs_and_demo_events(app: AppTest) -> None:
    app.selectbox(key="author").set_value("Camille").run()
    goto(app, EVENTS)
    tab = next(b for b in app.button_group if b.key == "ev_tab")
    tab.set_value("Équipe").run()
    next(b for b in app.button_group if b.key == "evs_status_Équipe").set_value("Tous").run()
    team = ev_grid(app)
    assert set(team["Titre"]) == {"Festive AOS Oct 14-21", "Lancement Advent", "Arrêt 7928.70"}
    assert set(team["Statut"]) == {"● À venir", "● Bloqué"}
    tab.set_value("Mes événements").run()
    mine = ev_grid(app)
    assert list(mine["Titre"]) == ["Demande B80"] and mine["Portée"][0] == "○ Personnel · Camille"


def test_create_event_from_the_side_panel(app: AppTest) -> None:
    app.selectbox(key="author").set_value("Léa").run()
    goto(app, EVENTS)
    app.button(key="ev_new").click().run()
    by_suffix(app.text_input, "_title").input("Interdiction test").run()
    by_suffix(app.selectbox, "_type_team").set_value("ban").run()
    by_suffix(app.text_area, "_skus").input("7005.70; 0005452\nZZZ-404 7005.70").run()
    assert any("1 inconnu" in m.value and "1 doublon" in m.value for m in app.markdown)
    by_suffix(app.button, "_save").click().run()
    assert not app.exception, app.exception
    from app.storage import db
    from app.storage import events as store

    conn = db.connect()
    events = {e.title: e for e in store.list_events(conn)}
    # texte (zéros gardés), doublon retiré, SKU inconnu gardé
    assert events["Interdiction test"].sku_codes() == ["7005.70", "0005452", "ZZZ-404"]
    assert events["Interdiction test"].end is None  # jusqu'à nouvel ordre
    assert store.event_history(conn, events["Interdiction test"].id)[0]["author"] == "Léa"


def test_personal_event_applies_only_to_its_planner_until_taken_over(app: AppTest) -> None:
    app.selectbox(key="author").set_value("Sami").run()
    compute_b80(app)
    lines = {x.sku: x for x in app.session_state["base"].lines}
    assert lines["7042.70"].qty == 1200  # la demande de Camille ne s'applique pas à Sami
    assert any("Demande B80" in i.value and "Camille" in i.value for i in app.info)
    # Lecture seule pour Sami, puis « Reprendre »
    goto(app, EVENTS)
    next(b for b in app.button_group if b.key == "ev_tab").set_value("Mes événements").run()
    app.session_state["ev_edit"] = 4
    app.run()
    assert any("lecture seule" in c.value for c in app.caption)
    by_suffix(app.button, "_take").click().run()
    assert app.session_state["taken_over"] == {4}
    compute_b80(app)
    lines = {x.sku: x for x in app.session_state["base"].lines}
    assert lines["7042.70"].qty == 1440  # 1200 + 240 demandés par la boutique
    assert "○ Demande B80" in grid(app).set_index("SKU").loc["7042.70", "Événement"]


def test_export_is_refused_until_the_gate_is_green(app: AppTest) -> None:
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    goto(app, PAGES[3])
    assert app.get("download_button")[0].proto.disabled  # garde-fou rouge
    app.button(key="order_gate_blocks").click().run()  # confirme le blocage de 7928.70
    app.button(key="order_gate_seen").click().run()
    assert app.get("download_button")[0].proto.disabled  # reste : lancements / demandes
    next(c for c in app.checkbox if c.key.startswith("order_gate_include")).check().run()
    assert not app.get("download_button")[0].proto.disabled
    from app.storage import db
    from app.storage import events as store

    kinds = {v["kind"] for v in store.list_validations(db.connect(), "B80")}
    assert kinds == {"confirm_block", "seen", "include"}


def test_store_request_ends_after_export_with_confirmation(app: AppTest) -> None:
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    goto(app, PAGES[3])
    app.session_state["requests_to_close"] = [4]  # ce que fait le clic sur « Télécharger »
    app.run()
    app.button(key="close_req_4").click().run()
    from app.domain.events import EventStatus
    from app.storage import db
    from app.storage import events as store

    conn = db.connect()
    assert store.get_event(conn, 4).status is EventStatus.DONE
    assert store.event_history(conn, 4)[0]["action"] == "terminé (commande exportée)"


def test_day_shows_events_and_gates_the_zip(app: AppTest) -> None:
    """Journée : colonne Événements par boutique et même garde-fou pour le zip."""
    from app.domain.models import ForecastMode, PlanningParameters
    from app.ingestion.masters import build_inputs_for_boutique
    from app.services.planning import EventContext, run_day
    from app.storage import db
    from app.storage import events as store

    app.selectbox(key="author").set_value("Camille").run()
    goto(app, PAGES[2])
    data = app.session_state["day"]
    params = PlanningParameters(boutique="B80", run_date=date(2026, 10, 5),
                                delivery_date=date(2026, 10, 8), cover_days=9,
                                coffee_cover_days=None)  # fmt: skip
    # Même calcul que le bouton « Calculer toutes les commandes » (la grille de saisie de la
    # Journée n'est pas pilotable dans AppTest).
    app.session_state["day_outcomes"] = run_day(
        [params], ForecastMode.STANDARD,
        build_inputs=lambda b: build_inputs_for_boutique(
            b, data.stock, data.movements, data.masters.products, data.masters.conversions,
            data.masters.dc_mapping, portfolio=data.masters.portfolio.get(b, {}),
            exclusions=data.exclusions),
        business_inputs=data.business,
        event_context=EventContext(tuple(store.list_events(db.connect())), "Camille"),
    )  # fmt: skip
    app.run()
    assert not app.exception, app.exception
    assert any(m.value == "● 5 · ○ 1" for m in app.markdown)  # SKU flagués équipe / personnel
    assert app.get("download_button")[0].proto.disabled
    app.button(key="day_gate_blocks").click().run()
    app.button(key="day_gate_seen").click().run()
    next(c for c in app.checkbox if c.key.startswith("day_gate_include")).check().run()
    assert not app.get("download_button")[0].proto.disabled
