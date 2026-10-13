"""Événements SKU : base locale (historique, planners, validations) et classeur Excel."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.domain.events import EventEffect, EventScope, EventSku, EventStatus, EventType, SkuEvent
from app.ingestion.events import (
    build_catalog,
    check_skus,
    events_workbook,
    example_events,
    read_events_workbook,
    split_skus,
)
from app.storage import db
from app.storage import events as store

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "evenements_sku.xlsx"


@pytest.fixture
def conn():  # noqa: ANN201
    c = db.connect(Path(":memory:"))
    yield c
    c.close()


def test_paste_keeps_skus_as_text_and_flags_unknown_and_duplicates() -> None:
    skus = split_skus("7005.70, 0005452;3517/BULK\n7005.7\tXYZ 9999.70")
    assert skus == ["7005.70", "0005452", "3517/BULK", "7005.7", "XYZ", "9999.70"]
    catalog = build_catalog([("7005.70", "VER-Melozio"), ("0005452", "Sac"), ("3517/BULK", None)])
    checks = {c.sku: c for c in check_skus(skus, catalog)}
    assert checks["7005.70"].description == "VER-Melozio" and checks["7005.70"].known
    assert checks["7005.7"].duplicate and checks["7005.7"].known  # 7005.7 = 7005.70
    assert not checks["9999.70"].known and not checks["XYZ"].known
    assert checks["0005452"].sku == "0005452"


def test_planners_first_name_only(conn) -> None:  # noqa: ANN001
    store.add_planner(conn, "  Camille ")
    store.add_planner(conn, "camille")  # même prénom, pas de doublon
    store.add_planner(conn, "Sami")
    assert store.list_planners(conn) == ["Camille", "Sami"]
    with pytest.raises(ValueError, match="pas d'email"):
        store.add_planner(conn, "camille@example.com")
    store.remove_planner(conn, "Sami")
    assert store.list_planners(conn) == ["Camille"]


def test_event_history_keeps_who_changed_what(conn) -> None:  # noqa: ANN001
    e = example_events()[0]
    saved = store.save_event(conn, e, "Camille")
    assert saved.id == 1 and saved.created_by == "Camille" and saved.created_at
    store.save_event(conn, saved.model_copy(update={"title": "Festive AOS v2"}), "Sami")
    store.set_status(conn, 1, EventStatus.CANCELLED, "Sami")
    store.log_takeover(conn, 1, "Léa")
    hist = store.event_history(conn, 1)
    assert [h["action"] for h in hist] == ["repris", "annulé", "modifié", "créé"]
    assert hist[2]["author"] == "Sami" and hist[2]["changes"]["title"] == [
        "Festive AOS Oct 14-21", "Festive AOS v2"]  # fmt: skip
    again = store.get_event(conn, 1)
    assert again is not None and again.status is EventStatus.CANCELLED
    assert again.created_by == "Camille" and again.skus[0].sku == "7072.70"
    with pytest.raises(ValueError, match="Choisis ton nom"):
        store.save_event(conn, e, " ")


def test_manual_stop_then_reactivate_is_traced(conn) -> None:  # noqa: ANN001
    """D-054 : « Terminer » met la fin à aujourd'hui (historique), « Réactiver » l'efface."""
    from app.engines.events import date_status

    fg = SkuEvent(scope=EventScope.TEAM, type=EventType.PROMOTION, title="Free Gift (FG)",
                  skus=(EventSku(sku="7005.70"),), start=date(2026, 10, 6),
                  end=date(2026, 10, 22), effect=EventEffect.FLAG)  # fmt: skip
    store.save_event(conn, fg, "Camille")
    today = date(2026, 10, 12)
    stopped = store.stop_event(conn, 1, "Sami", today)
    assert stopped.end == today and stopped.stopped_by == "Sami"
    assert stopped.end_before_stop == date(2026, 10, 22)
    assert date_status(stopped, today) == "Terminé"
    action = store.event_history(conn, 1)[0]["action"]
    assert action == "terminé manuellement par Sami le 12 oct. 2026"
    back = store.reactivate_event(conn, 1, "Sami")
    assert back.end == date(2026, 10, 22) and back.stopped_on is None
    assert date_status(back, today) == "Actif"
    assert store.event_history(conn, 1)[0]["action"] == "réactivé (fin de l'arrêt effacée)"


def test_old_done_status_becomes_a_dated_manual_stop() -> None:
    """Un ancien événement « Terminé » (statut manuel) est relu comme un arrêt daté."""
    base = {"scope": "team", "type": "promotion", "title": "Ancien", "effect": "flag",
            "skus": [{"sku": "7005.70"}], "start": "2026-10-01", "status": "done"}  # fmt: skip
    e = SkuEvent.model_validate({**base, "updated_at": "2026-10-09T10:00:00"})
    assert e.status is EventStatus.ACTIVE and e.end == date(2026, 10, 9)
    assert e.stopped_on == date(2026, 10, 9) and e.end_before_stop is None
    early = SkuEvent.model_validate({**base, "start": "2026-11-01",
                                     "updated_at": "2026-10-09T10:00:00"})  # fmt: skip
    assert early.status is EventStatus.CANCELLED  # arrêté avant même de commencer


def test_validations_are_traced(conn) -> None:  # noqa: ANN001
    store.record_validation(conn, author="Camille", boutique="B80", delivery=date(2026, 10, 8),
                            kind="confirm_block", skus=["7921.70"])  # fmt: skip
    rows = store.list_validations(conn, "B80")
    assert rows[0]["author"] == "Camille" and rows[0]["skus"] == ["7921.70"] and rows[0]["at"]


def test_template_exists_and_roundtrips_with_fictional_examples() -> None:
    data = TEMPLATE.read_bytes()
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Événements", "Quantités", "SKU détail", "Lisez-moi"]
    res = read_events_workbook(data, TEMPLATE.name, "Camille")
    assert not res.problems, res.problems
    assert [e.title for e in res.events] == [e.title for e in example_events()]
    advent = res.events[1]
    assert advent.skus[0].multiple == 6 and advent.dcs == ("CY1",) and advent.lead_days == 7
    assert res.events[3].until_next_order and res.events[3].scope is EventScope.PERSONAL


def test_import_dates_are_day_month_year() -> None:
    """D-054 : jour/mois/année (ou « 22 oct. 2026 ») ; mois d'abord, année à 2 chiffres et
    statut « Terminé » refusés avec leur raison."""
    fg = example_events()[-1]
    wb = load_workbook(io.BytesIO(events_workbook([fg])))
    ws = wb["Événements"]
    head = [c.value for c in ws[1]]
    assert ws.cell(2, head.index("Fin") + 1).value == "22/10/2026"
    cases = {"22/10/2026": date(2026, 10, 22), "22 oct. 2026": date(2026, 10, 22),
             "2026-10-22": date(2026, 10, 22), "10/22/2026": "jour/mois/année",
             "22/10/26": "année sur 4 chiffres"}  # fmt: skip
    for text, expected in cases.items():
        ws.cell(2, head.index("Fin") + 1).value = text
        buf = io.BytesIO()
        wb.save(buf)
        res = read_events_workbook(buf.getvalue(), "e.xlsx", "Camille")
        if isinstance(expected, date):
            assert res.events[0].end == expected, text
        else:
            assert expected in res.problems[0], (text, res.problems)
    ws.cell(2, head.index("Fin") + 1).value = "22/10/2026"
    ws.cell(2, head.index("Statut") + 1).value = "Terminé"
    buf = io.BytesIO()
    wb.save(buf)
    res = read_events_workbook(buf.getvalue(), "e.xlsx", "Camille")
    assert "mets une date de fin" in res.problems[0]


def test_import_reports_incomplete_rows_instead_of_ignoring_them() -> None:
    from openpyxl import Workbook

    good = SkuEvent(scope=EventScope.TEAM, type=EventType.BAN, title="OK", effect=EventEffect.BLOCK,
                    skus=(EventSku(sku="0005452"),), start=date(2026, 10, 5))  # fmt: skip
    wb = load_workbook(io.BytesIO(events_workbook([good])))
    ws = wb["Événements"]
    ws.append(["E2", "Équipe", "Promotion", "Sans fin", "", "7005.70", "", "", "2026-10-05", "",
               0, "Flag seulement"])  # fmt: skip
    ws.append(["E3", "Équipe", "Promotion", "Sans SKU", "", "", "", "", "2026-10-05",
               "2026-10-09", 0, "Flag seulement"])  # fmt: skip
    ws.append(["E4", "Équipe", "Type bizarre", "X", "", "1", "", "", "2026-10-05"])
    buf = io.BytesIO()
    wb.save(buf)
    res = read_events_workbook(buf.getvalue(), "e.xlsx", "Camille")
    assert [e.title for e in res.events] == ["OK", "Sans fin"]  # fin vide permise (D-053)
    assert res.events[0].skus[0].sku == "0005452" and res.events[1].end is None
    assert len(res.problems) == 2
    assert "aucun SKU" in res.problems[0] and "Type bizarre" in res.problems[1]
    del Workbook


def test_stop_list_is_seeded_once_as_a_team_stop_without_end(conn) -> None:  # noqa: ANN001
    """Liste du planner du 13-oct-2026 : 17 SKU à ne plus envoyer, un seul Arrêt d'envoi."""
    from app.ingestion.events import STOP_LIST_SKUS, starter_events

    (e,) = starter_events()
    assert e.type is EventType.STOP and e.effect is EventEffect.BLOCK
    assert e.scope is EventScope.TEAM and e.all_boutiques and e.end is None
    assert len(STOP_LIST_SKUS) == 17 and e.sku_codes() == list(STOP_LIST_SKUS)
    assert all(isinstance(s, str) for s in e.sku_codes()) and "7921.70" in e.sku_codes()
    assert "BUNDLE" in e.description
    assert store.seed_events(conn, starter_events(), "Données de départ") == 1
    store.set_status(conn, 1, EventStatus.CANCELLED, "Sami")
    assert store.seed_events(conn, starter_events(), "Données de départ") == 0  # pas recréé
    assert len(store.list_events(conn)) == 1


def test_stop_list_skus_are_in_the_referential_except_165332() -> None:
    """Rapprochement avec la Multiple list des fixtures (7921.7 → 7921.70)."""
    from app.ingestion.events import STOP_LIST_SKUS

    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "b80_2026-10-05"
    lines = (fixtures / "master_multiples.csv").read_text(encoding="utf-8").splitlines()
    known = {line.split(",")[0] for line in lines[1:]}
    assert [s for s in STOP_LIST_SKUS if s not in known] == ["165332"]
