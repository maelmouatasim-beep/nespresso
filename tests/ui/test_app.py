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


def compute_b80(at: AppTest, mode: str = "Safe") -> None:
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
    assert list(recap.columns) == ["Boutique", "État", "Livraison", "Unités", "Palettes",
                                   "À vérifier"]  # fmt: skip
    assert any("Récapitulatif" in e.label for e in app.expander)  # replié par défaut (D-050)
    labels = {b.key: b.label for b in app.button if b.key and b.key.startswith("tab_open_")}
    assert labels[f"tab_open_{other}"] == f"○ {other} · à calculer"
    assert labels["tab_open_B80"] == "● B80 · prête"  # tout l'onglet « À vérifier » confirmé
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
    from app.engines.events import event_tags
    from app.ui.common import is_inactive_line
    from app.ui.screens.order import verify_rank

    all_lines = app.session_state["base"].lines
    base = {x.sku: x for x in app.session_state["base"].base_lines}
    inactive = [x for x in all_lines if is_inactive_line(x)]
    # D-050 : l'onglet « À vérifier » est ouvert par défaut : BLOCKED, puis REVIEW, puis les
    # lignes OK flaguées par un événement, chaque groupe par Qty proposée décroissante.
    df = grid(app)
    assert app.session_state["f_status"] == "À vérifier"
    ranks = [verify_rank(base[s]) for s in df["SKU"]]
    assert ranks == sorted(ranks) and {0, 1} <= set(ranks)
    assert all(base[s].status.value != "OK" or event_tags(base[s]) for s in df["SKU"])
    for r in (0, 1, 2):
        qtys = [base[s].qty for s, k in zip(df["SKU"], ranks, strict=True) if k == r]
        assert qtys == sorted(qtys, reverse=True)
    assert any("À vérifier" in m.label for m in app.metric)
    status_tab = next(b for b in app.button_group if b.key == "f_status")
    status_tab.set_value("Toutes").run()
    df = grid(app)
    # D-049 : une seule grille, toutes les lignes sauf les anciens SKU (tout à zéro), masqués.
    assert len(df) == len(all_lines) - len(inactive)
    assert inactive and not {x.sku for x in inactive} & set(df["SKU"])
    app.checkbox(key="show_inactive").check().run()
    assert len(grid(app)) == len(all_lines)
    app.checkbox(key="show_inactive").uncheck().run()
    df = grid(app)
    flagged = ~df["Statut"].str.contains("OK")
    first_ok = int(flagged.values.argmin())
    assert flagged[:first_ok].all() and not flagged[first_ok:].any()  # REVIEW/BLOCKED en haut
    ok = df[first_ok:]["Qty proposée"].tolist()
    assert ok == sorted(ok, reverse=True)  # puis Qty proposée décroissante
    assert list(df.columns) == ["Confirmé", "Détail", "Statut", "SKU", "Description", "Available",
                                "Expected", "Ventes", "Qty proposée", "Qty finale", "Couverture",
                                "Multiple", "Stock DC", "Raisons"]  # D-052  # fmt: skip

    next(b for b in app.button_group if b.key == "f_cat").set_value("Cafés").run()
    types = {x.sku: x.product_type for x in app.session_state["base"].lines}
    assert {types[s] for s in grid(app)["SKU"]} == {"C"}
    next(b for b in app.button_group if b.key == "f_status").set_value("OK").run()
    assert grid(app)["Statut"].str.contains("OK").all()  # OK non confirmées, sans événement
    assert not any(event_tags(base[s]) for s in grid(app)["SKU"])
    app.checkbox(key="f_qty").check().run()
    assert (grid(app)["Qty finale"] > 0).all()


def test_accept_all_ok_marks_ok_lines(app: AppTest) -> None:
    compute_b80(app)
    assert not [b for b in app.button if b.key == "accept_ok"]  # seulement sur l'onglet OK
    next(b for b in app.button_group if b.key == "f_status").set_value("OK").run()
    app.button(key="accept_ok").click().run()
    assert len(grid(app)) == 0  # l'onglet OK se vide
    next(b for b in app.button_group if b.key == "f_status").set_value("Toutes").run()
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
    assert grid(app).set_index("SKU").loc["7042.70", "Raisons"].startswith("Demande B80")
    app.session_state["why_sku"] = "7042.70"
    app.run()
    assert any("○ Demande B80" in m.value for m in app.markdown)  # dans le « Pourquoi » (D-046)
    assert any("en route" in m.value for m in app.markdown)  # Available + écart (D-047)


def status_tab(at: AppTest, value: str) -> None:
    next(b for b in at.button_group if b.key == "f_status").set_value(value).run()


def test_export_waits_for_the_verify_tab_to_be_empty(app: AppTest) -> None:
    """D-050 : plus de seconde liste ; le LT attend que l'onglet « À vérifier » soit vide."""
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    assert download(app, "Télécharger le LT").disabled
    assert download(app, "Télécharger tous les LT").disabled
    df = grid(app)  # onglet « À vérifier » ouvert par défaut
    base = {x.sku: x for x in app.session_state["base"].base_lines}
    n = len(df)
    assert any(f"{n} ligne(s) à vérifier avant l'export" in m.value for m in app.markdown)
    assert not [d for d in app.dataframe if d.key and d.key.startswith("pending_")]
    assert not df["Confirmé"].any()
    # REVIEW et BLOCKED non confirmés, plus les lignes OK flaguées par un événement (lancement,
    # demande boutique…) avec le titre de l'événement en raison.
    assert {base[s].status.value for s in df["SKU"]} == {"REVIEW", "BLOCKED"}
    assert "Advent" in df.set_index("SKU").loc["ADV-2026-CA", "Raisons"]
    assert not [
        b for b in app.button if b.key and b.key.startswith("order_gate")
    ]  # plus de garde-fou
    # Confirmer une ligne : elle quitte « À vérifier » et passe « Confirmé » dans « Toutes ».
    sku = df.iloc[0]["SKU"]
    app.session_state["seen"] = app.session_state["seen"] | {sku}
    app.run()
    assert len(grid(app)) == n - 1 and sku not in set(grid(app)["SKU"])
    status_tab(app, "Toutes")
    assert grid(app).set_index("SKU").loc[sku, "Statut"] == "● Confirmé"
    status_tab(app, "À vérifier")
    app.button(key="confirm_shown").click().run()  # « Confirmer les lignes affichées »
    # D-051 : plus de double confirmation DC à 0 (D-048) : tout l'onglet se confirme d'un coup.
    assert len(grid(app)) == 0
    assert any("Tout est confirmé" in m.value for m in app.markdown)
    assert not [b for b in app.button if b.key == "see_pending"]
    assert "dc_warn" not in app.session_state
    assert not download(app, "Télécharger le LT").disabled
    assert not download(app, "Télécharger tous les LT").disabled  # une boutique prête
    from app.storage import db
    from app.storage import events as store

    validations = store.list_validations(db.connect(), "B80")
    assert {v["kind"] for v in validations} == {"confirm_block", "seen"}


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
    status_tab(app, "Toutes")  # 7005.70 est OK : hors de l'onglet « À vérifier » (D-050)
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
    df = grid(app)  # onglet « À vérifier » : première ligne REVIEW (après les BLOCKED)
    pos = int(df["Statut"].str.contains("REVIEW").values.argmax())
    sku, qty, multiple = df.iloc[pos][["SKU", "Qty finale", "Multiple"]]
    n_check = len(df)
    app.session_state["why_sku"] = sku
    app.run()
    app.number_input(key=f"dlg_qty_{sku}").set_value(int(qty + multiple)).run()
    app.button(key="dlg_save").click().run()
    assert sku in app.session_state["overrides"]
    df = grid(app)  # toujours dans « À vérifier », même place, statut REVIEW + marqueur
    assert len(df) == n_check and df.iloc[pos]["SKU"] == sku
    assert df.iloc[pos]["Statut"] == "● REVIEW · modifié"
    assert df.iloc[pos]["Qty finale"] == qty + multiple
    app.checkbox(key="f_mod").check().run()  # « Modifiées seulement »
    assert list(grid(app)["SKU"]) == [sku]
    app.checkbox(key="f_mod").uncheck().run()
    # Confirmer (case « Confirmé » ou bouton du panneau) : la ligne quitte « À vérifier ».
    app.button(key="why_seen").click().run()
    assert sku not in set(grid(app)["SKU"]) and len(grid(app)) == n_check - 1
    assert download(app, "Télécharger le LT").disabled  # d'autres lignes à vérifier
    status_tab(app, "Confirmées")
    row = grid(app).set_index("SKU").loc[sku]
    assert row["Statut"] == "● Confirmé · modifié" and row["Confirmé"]
    # Décocher la ramène dans « À vérifier » ; « Voir » sous la table y ramène aussi.
    app.session_state["why_sku"] = sku
    app.run()
    app.button(key="why_seen").click().run()
    app.button(key="see_pending").click().run()
    assert app.session_state["f_status"] == "À vérifier"
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


def test_expected_and_cover_follow_the_final_quantity(app: AppTest) -> None:
    """D-052 : « Expected » affiché = dispo + en route + Qty finale et « Couverture » suivent
    chaque saisie ; le moteur part toujours de l'Expected de la Stock Situation."""
    app.selectbox(key="author").set_value("Camille").run()
    compute_b80(app)
    status_tab(app, "Toutes")
    line = next(x for x in app.session_state["base"].lines if x.sku == "7005.70")
    inp = line.explanation["inputs"]
    today, sales = inp["expected_total"], inp["sales_total"]
    days = line.explanation["parameters"]["sales_history_days"]
    row = grid(app).set_index("SKU").loc["7005.70"]
    assert row["Expected"] == today + 3120  # Qty proposée 3120 déjà comprise
    assert row["Available"] == inp["available_total"]
    assert "Expected + commande" not in row.index
    # D-047 : Raisons en texte court = source, événement, raisons coupées ; sans doublon
    assert row["Raisons"].split(" | ")[0] == "Prévision"
    assert row["Couverture"] == round((today + 3120) / sales * days, 1)
    app.session_state["why_sku"] = "7005.70"
    app.run()
    app.number_input(key="dlg_qty_7005.70").set_value(3600).run()
    app.button(key="dlg_save").click().run()
    row = grid(app).set_index("SKU").loc["7005.70"]
    assert row["Expected"] == today + 3600  # monte en direct avec la saisie
    assert row["Couverture"] == round((today + 3600) / sales * days, 1)
    assert inp["expected_total"] == today  # le moteur part toujours de la Stock Situation
    from app.ui.common import fmt_int

    why = next(m.value for m in app.markdown if "Expected affiché" in m.value)
    assert (
        f"dispo {fmt_int(inp['available_total'])}" in why and f"+ commande {fmt_int(3600)}" in why
    )
    assert any("Couverture : actuelle" in m.value for m in app.markdown)


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


def test_safe_mode_caps_to_dc_and_override_above_dc_needs_confirmation(app: AppTest) -> None:
    """D-051 : plafond au stock DC (mode Safe, défaut), filtre « Plafonnés DC », saisie
    au-dessus du DC → REVIEW « Dépasse le stock DC » ; parité Excel jamais plafonnée."""
    from app.ui.common import is_inactive_line

    app.selectbox(key="author").set_value("Camille").run()
    assert app.session_state["orders"] == {}
    compute_b80(app)
    assert app.session_state["orders"]["B80"]["params"]["mode"] == "Safe"
    lines = {x.sku: x for x in app.session_state["base"].lines}
    capped = {
        s for s, x in lines.items() if x.explanation.get("dc_cap") and not is_inactive_line(x)
    }
    assert capped
    for s in capped:
        cap = lines[s].explanation["dc_cap"]
        assert lines[s].qty == cap["qty"] < cap["need"] and lines[s].qty % cap["multiple"] == 0
        assert lines[s].qty <= cap["dc"] and lines[s].status.value != "BLOCKED"
        assert not any("Stock DC insuffisant" in r for r in lines[s].reasons)
    status_tab(app, "Toutes")
    app.checkbox(key="f_cap").check().run()
    df = grid(app).set_index("SKU")
    assert set(df.index) == capped
    assert df["Raisons"].str.contains("Plafonné au stock DC").all()
    sku = next(s for s in capped if lines[s].status.value == "OK" and lines[s].qty > 0)
    dc = lines[sku].explanation["inputs"]["dc_available"]
    assert df.loc[sku, "Statut"] == "● OK"
    # Saisie au-dessus du stock DC : acceptée, mais REVIEW, à confirmer avant l'export.
    app.session_state["why_sku"] = sku
    app.run()
    above = int(lines[sku].qty + lines[sku].explanation["multiple"])
    assert above > dc
    app.number_input(key=f"dlg_qty_{sku}").set_value(above).run()
    app.button(key="dlg_save").click().run()
    row = grid(app).set_index("SKU").loc[sku]
    assert (row["Statut"], row["Qty finale"]) == ("● REVIEW · modifié", above)
    assert "Dépasse le stock DC" in row["Raisons"]
    status_tab(app, "À vérifier")
    assert sku in set(grid(app)["SKU"])
    # Parité Excel : rien n'est plafonné, le signal REVIEW « Stock DC insuffisant » reste.
    compute_b80(app, mode="Parité Excel")
    par = {x.sku: x for x in app.session_state["base"].lines}
    assert not any(x.explanation.get("dc_cap") for x in par.values())
    assert any("Stock DC insuffisant" in r for x in par.values() for r in x.reasons)
