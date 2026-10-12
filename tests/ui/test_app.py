"""Tests de bout en bout de l'interface Streamlit (sans navigateur)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[2] / "app" / "ui" / "main.py")
PAGES = ("1. Dépôt du jour", "2. Commandes", "3. Export", "4. Événements SKU",
         "5. Référentiels", "6. Historique")  # fmt: skip
ORDERS, EXPORT, EVENTS, REFS = PAGES[1], PAGES[2], PAGES[3], PAGES[4]


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


def add_boutique(at: AppTest, boutique: str) -> None:
    goto(at, ORDERS)
    at.selectbox(key="add_boutique").set_value(boutique).run()
    assert not at.exception, at.exception


def compute_b80(at: AppTest, mode: str = "Standard") -> None:
    if "B80" not in at.session_state["orders"]:
        add_boutique(at, "B80")
    goto(at, ORDERS)
    at.date_input(key="p_delivery_B80").set_value(date(2026, 10, 8)).run()
    at.number_input(key="p_cover_B80").set_value(9.0).run()
    at.selectbox(key="p_mode_B80").set_value(mode).run()
    at.button(key="compute_B80").click().run()
    assert not at.exception, at.exception


def download(at: AppTest, label: str):  # noqa: ANN201
    """Bouton de téléchargement dont le libellé commence par `label` (son état : .disabled)."""
    return next(d.proto for d in at.get("download_button") if d.proto.label.startswith(label))


def test_every_screen_renders(app: AppTest) -> None:
    for page in PAGES:
        goto(app, page)
    goto(app, PAGES[0])
    assert any(b.key == "ready" for b in app.button)


def test_order_requires_parameters_then_computes(app: AppTest) -> None:
    add_boutique(app, "B80")
    app.button(key="compute_B80").click().run()
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
    goto(app, EXPORT)
    assert any("Choisis ton prénom" in e.value for e in app.error)
    app.selectbox(key="author").set_value("Sami").run()
    assert not app.exception
    app.button(key="save_run").click().run()
    assert app.session_state["saved_run_id"] == 1


def test_orders_start_empty_without_schedule_preselection(app: AppTest) -> None:
    goto(app, ORDERS)
    assert app.session_state["orders"] == {}
    assert app.selectbox(key="add_boutique").value is None
    assert not [d for d in app.dataframe if d.key and d.key.startswith("grid_")]


def test_second_boutique_takes_first_parameters_and_keeps_each_order(app: AppTest) -> None:
    compute_b80(app)
    app.button(key="confirm_shown").click().run()
    confirmed = set(app.session_state["seen"])
    other = next(o for o in app.selectbox(key="add_boutique").options if not o.startswith("B80"))
    other = other.split(" — ")[0]
    add_boutique(app, other)
    params = app.session_state["orders"][other]["params"]
    assert (params["delivery"], params["cover"]) == (date(2026, 10, 8), 9.0)  # repris, modifiables
    assert app.session_state["active"] == "B80"  # l'onglet affiché ne change pas
    app.button(key=f"tab_open_{other}").click().run()
    assert app.session_state["active"] == other and app.session_state["base"] is None
    app.button(key="tab_open_B80").click().run()
    assert app.session_state["seen"] == confirmed  # la commande de B80 est gardée telle quelle
    recap = next(d.value for d in app.dataframe if "Palettes" in d.value.columns)
    assert list(recap["Boutique"]) == ["B80", other, "Total"]
    # Le zip attend chaque boutique : celle qui n'est pas calculée est citée.
    app.selectbox(key="author").set_value("Camille").run()
    assert download(app, "Télécharger tous les LT").disabled
    assert any(other in c.value and "à calculer" in c.value for c in app.caption)
    app.button(key=f"tab_close_{other}").click().run()
    assert other not in app.session_state["orders"]


def test_referential_owner_is_saved(app: AppTest) -> None:
    goto(app, REFS)
    app.text_input(key="owner_produits").input("Équipe planning MTL").run()
    assert not app.exception
    from app.ui.workspace import demo_workspace

    assert demo_workspace().meta()["produits"]["owner"] == "Équipe planning MTL"


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
    assert list(df.columns[:6]) == ["Confirmé", "Détail", "Statut", "SKU", "Description",
                                    "Catégorie"]  # fmt: skip

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
    base = {x.sku: x.status.value for x in app.session_state["base"].base_lines}
    ok = df[df["SKU"].map(base) == "OK"]
    assert len(ok) and (ok["Statut"] == "● Confirmé").all() and ok["Confirmé"].all()
    review = df[df["SKU"].map(base) == "REVIEW"]
    assert (review["Statut"] == "● REVIEW").all() and not review["Confirmé"].any()


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


def pending(at: AppTest):  # noqa: ANN201
    return next((d.value for d in at.dataframe if d.key and d.key.startswith("pending_")), None)


def test_export_waits_for_the_pending_list_to_be_empty(app: AppTest) -> None:
    """D-043 : une seule liste « X lignes ne partiront pas si tu ne les confirmes pas »."""
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    assert download(app, "Télécharger le LT").disabled
    assert download(app, "Télécharger tous les LT").disabled
    df = pending(app)
    base = {x.sku: x for x in app.session_state["base"].base_lines}
    n = len(df)
    assert any(f"{n} ligne(s) ne partiront pas" in m.value for m in app.markdown)
    assert list(df.columns) == [
        "Confirmé",
        "Statut",
        "SKU",
        "Description",
        "Qty proposée",
        "Raisons",
    ]
    assert not df["Confirmé"].any()
    # REVIEW et BLOCKED non confirmés, plus les lignes OK flaguées par un événement (lancement,
    # demande boutique…) avec le titre de l'événement en raison.
    assert {base[s].status.value for s in df["SKU"]} == {"REVIEW", "BLOCKED"}
    assert "Advent" in df.set_index("SKU").loc["ADV-2026-CA", "Raisons"]
    assert not [
        b for b in app.button if b.key and b.key.startswith("order_gate")
    ]  # plus de garde-fou
    # Confirmer une ligne dans la liste : elle en sort et passe « Confirmé » dans la table.
    sku = df.iloc[0]["SKU"]
    app.session_state["seen"] = app.session_state["seen"] | {sku}
    app.run()
    assert len(pending(app)) == n - 1 and sku not in set(pending(app)["SKU"])
    assert grid(app).set_index("SKU").loc[sku, "Statut"] == "● Confirmé"
    app.button(key="confirm_pending").click().run()  # « Tout confirmer »
    assert pending(app) is None
    assert any("Tout est confirmé" in m.value for m in app.markdown)
    assert not download(app, "Télécharger le LT").disabled
    assert not download(app, "Télécharger tous les LT").disabled  # une boutique prête
    from app.storage import db
    from app.storage import events as store

    kinds = {v["kind"] for v in store.list_validations(db.connect(), "B80")}
    assert kinds == {"confirm_block", "seen"}


def test_store_request_ends_after_export_with_confirmation(app: AppTest) -> None:
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    app.session_state["requests_to_close"] = [4]  # ce que fait le clic sur « Télécharger »
    app.run()
    app.button(key="close_req_4").click().run()
    from app.domain.events import EventStatus
    from app.storage import db
    from app.storage import events as store

    conn = db.connect()
    assert store.get_event(conn, 4).status is EventStatus.DONE
    assert store.event_history(conn, 4)[0]["action"] == "terminé (commande exportée)"


def test_quantity_change_counts_immediately_with_deduced_context(app: AppTest) -> None:
    """D-038 : pas de catégorie ; la qty saisie compte tout de suite, arrondie au multiple avec
    un avertissement ; le contexte déduit n'apparaît que dans « Pourquoi »."""
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    assert "Catégorie modif." not in grid(app).columns
    app.session_state["why_sku"] = "7005.70"
    app.run()
    assert not [s for s in app.selectbox if s.key.startswith("dlg_cat")]
    app.number_input(key="dlg_qty_7005.70").set_value(3350).run()
    app.button(key="dlg_save").click().run()
    ov = app.session_state["overrides"]["7005.70"]
    assert (ov.qty_before, ov.qty_after, ov.author, ov.category) == (3120, 3350, "Camille", None)
    assert ov.context_detail  # déduit, jamais demandé
    assert any("n'est pas un multiple de 240, arrondi à 3360" in c.value for c in app.caption)
    row = grid(app).set_index("SKU").loc["7005.70"]
    assert (row["Statut"], row["Qty finale"]) == ("● OK · modifié", 3360)  # D-039
    assert ov.context_detail not in row["Raisons"]
    assert any("Contexte déduit" in m.value for m in app.markdown)
    app.run()  # l'avertissement d'arrondi ne s'affiche qu'une fois
    assert not any("pas un multiple" in c.value for c in app.caption)


def test_referentials_have_three_tabs_all_editable(app: AppTest) -> None:
    goto(app, REFS)
    assert {"Produits", "Boutiques", "Planners", "Portfolio"} <= {t.label for t in app.tabs}
    keys = {b.key for b in app.button}
    assert {"save_products", "save_boutiques", "save_part_portfolio"} <= keys
    app.toggle(key="prod_anom").set_value(True).run()
    assert not app.exception
    app.selectbox(key="author").set_value("Sami").run()
    app.button(key="save_products").click().run()
    assert not app.exception and not app.error
    app.selectbox(key="btq_open").set_value("B80").run()
    assert {"save_part_rules", "save_part_slots"} <= {b.key for b in app.button}
    app.button(key="save_boutiques").click().run()
    assert not app.exception and not app.error


def test_changing_a_quantity_never_changes_the_status(app: AppTest) -> None:
    """D-039 : modifier et confirmer sont deux actions distinctes."""
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    next(b for b in app.button_group if b.key == "f_status").set_value("REVIEW").run()
    df = grid(app)
    sku, qty, multiple = df.iloc[0][["SKU", "Qty finale", "Multiple"]]
    n_review = len(df)
    app.session_state["why_sku"] = sku
    app.run()
    app.number_input(key=f"dlg_qty_{sku}").set_value(int(qty + multiple)).run()
    app.button(key="dlg_save").click().run()
    assert sku in app.session_state["overrides"]
    df = grid(app)  # toujours dans le filtre REVIEW, même place, statut REVIEW + marqueur
    assert len(df) == n_review and df.iloc[0]["SKU"] == sku
    assert df.iloc[0]["Statut"] == "● REVIEW · modifié"
    assert df.iloc[0]["Qty finale"] == qty + multiple
    next(b for b in app.button_group if b.key == "f_status").set_value("Modifié").run()
    assert list(grid(app)["SKU"]) == [sku]
    # Confirmer (case « Confirmé » ou bouton du panneau) : la ligne sort du filtre REVIEW.
    next(b for b in app.button_group if b.key == "f_status").set_value("REVIEW").run()
    app.button(key="why_seen").click().run()
    assert sku not in set(grid(app)["SKU"]) and len(grid(app)) == n_review - 1
    assert download(app, "Télécharger le LT").disabled  # d'autres REVIEW non confirmés
    next(b for b in app.button_group if b.key == "f_status").set_value("Confirmé").run()
    row = grid(app).set_index("SKU").loc[sku]
    assert row["Statut"] == "● Confirmé · modifié" and row["Confirmé"]
    # Décocher la ramène dans REVIEW.
    app.session_state["why_sku"] = sku
    app.run()
    app.button(key="why_seen").click().run()
    next(b for b in app.button_group if b.key == "f_status").set_value("REVIEW").run()
    assert sku in set(grid(app)["SKU"])


def test_old_allocation_file_is_converted_into_an_event(app: AppTest, tmp_path) -> None:  # noqa: ANN001
    """D-042 : l'ancien fichier compte encore, puis « Convertir » crée l'événement (même qty)."""
    masters = tmp_path / "data" / "demo" / "masters"
    (masters / "allocations.csv").write_text(
        "boutique,sku,total_qty,already_sent,wave_plan,current_wave,comment\n"
        "B80,7005.70,4800,0,75/25,1,promo test\n", encoding="utf-8")  # fmt: skip
    app.selectbox(key="author").set_value("Camille").run()
    app.session_state["day"] = None  # fichier posé après le chargement de la démo
    goto(app, REFS)
    assert not any("Allocations" in e.label for e in app.expander)
    compute_b80(app)
    assert {x.sku: x for x in app.session_state["base"].lines}["7005.70"].qty == 3600
    goto(app, EVENTS)
    assert any("Anciens fichiers encore utilisés" in w.value for w in app.warning)
    app.button(key="ev_migrate").click().run()
    assert not app.exception, app.exception
    assert not (masters / "allocations.csv").exists()
    assert list((masters / "_archives").glob("*_allocations.csv"))
    from app.domain.events import EventEffect
    from app.storage import db
    from app.storage import events as store

    created = [e for e in store.list_events(db.connect()) if e.effect is EventEffect.ALLOCATION]
    assert [(e.title, e.quantities[0].waves) for e in created] == [("Allocations B80 (reprise)",
                                                                    (75, 25))]  # fmt: skip
    goto(app, EVENTS)
    assert not any("Anciens fichiers" in w.value for w in app.warning)
    app.session_state["orders"].clear()
    compute_b80(app)
    line = {x.sku: x for x in app.session_state["base"].lines}["7005.70"]
    assert line.qty == 3600 and any("Allocations B80 (reprise)" in r for r in line.reasons)


def test_create_a_target_stock_event(app: AppTest) -> None:
    app.selectbox(key="author").set_value("Léa").run()
    goto(app, EVENTS)
    app.button(key="ev_new").click().run()
    by_suffix(app.text_input, "_title").input("Cible test").run()
    by_suffix(app.selectbox, "_type_team").set_value("target_stock").run()
    by_suffix(app.text_area, "_skus").input("7005.70").run()
    grid = next(d for d in app.dataframe if d.key and d.key.endswith("qty_target"))
    assert list(grid.value.columns) == ["Boutique", "SKU", "Cible", "Min", "Max"]
    # Sans cible ni min / max : la raison est affichée et l'enregistrement reste fermé.
    assert by_suffix(app.button, "_save").disabled
    assert any("Cible vide" in e.value for e in app.error)


def test_expected_follows_the_final_quantity(app: AppTest) -> None:
    """D-044 : la colonne Expected = Expected du jour + Qty finale, et suit chaque saisie."""
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    line = next(x for x in app.session_state["base"].lines if x.sku == "7005.70")
    today = line.explanation["inputs"]["expected_total"]
    row = grid(app).set_index("SKU").loc["7005.70"]
    assert row["Expected"] == today + 3120
    app.session_state["why_sku"] = "7005.70"
    app.run()
    app.number_input(key="dlg_qty_7005.70").set_value(3600).run()
    app.button(key="dlg_save").click().run()
    assert grid(app).set_index("SKU").loc["7005.70", "Expected"] == today + 3600
    assert any("Expected après commande" in m.value for m in app.markdown)


def test_several_skus_picked_in_one_promotion(app: AppTest) -> None:
    """Sélection multiple avec recherche : la liste se met à jour tout de suite."""
    app.selectbox(key="author").set_value("Léa").run()
    goto(app, EVENTS)
    app.button(key="ev_new").click().run()
    by_suffix(app.text_input, "_title").input("Promo plusieurs SKU").run()
    by_suffix(app.multiselect, "_skus_pick").set_value(["7005.70", "7072.70"]).run()
    table = next(d for d in app.dataframe if d.key and "_skutab_" in d.key).value
    assert list(table["SKU"]) == ["7005.70", "7072.70"]
    by_suffix(app.text_area, "_skus").input("7087.70\n7005.70").run()  # collage : doublon retiré
    assert by_suffix(app.multiselect, "_skus_pick").value == ["7005.70", "7072.70", "7087.70"]
    by_suffix(app.date_input, "_end_promotion").set_value(date(2026, 10, 20)).run()
    by_suffix(app.button, "_save").click().run()
    assert not app.exception, app.exception
    from app.storage import db
    from app.storage import events as store

    saved = {e.title: e for e in store.list_events(db.connect())}["Promo plusieurs SKU"]
    assert saved.sku_codes() == ["7005.70", "7072.70", "7087.70"]
