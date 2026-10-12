"""Commande d'une boutique (onglet de la page Commandes) : tuiles, une seule grille, « Pourquoi »."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.domain.events import EventScope
from app.domain.models import RecommendationLine, Status
from app.engines.categories import CATEGORIES
from app.engines.events import blocked_by_event, event_tags, select_for_order
from app.engines.overrides import OverrideError
from app.engines.pallets import MACHINES, OL, OTHERS, VL
from app.services.planning import with_overrides
from app.ui.common import (
    CONFIRMED_MD,
    EDITABLE_COLUMNS,
    EVENT_DOT,
    MODE_LABEL,
    MODIFIED_MD,
    ORDER_COLUMN_CONFIG,
    SOURCE_LABEL,
    STATUS_MD,
    all_events,
    author,
    boutique_label,
    category_of_line,
    cover_after,
    cover_target,
    day_data,
    dc_short,
    default_order,
    event_cell,
    explain_steps,
    fmt_1,
    fmt_date,
    fmt_int,
    incoming_of,
    is_confirmed,
    is_inactive_line,
    make_override,
    order_frame,
    rounding_note,
    shown_status,
    ss,
    style_order_frame,
)

CATEGORY_FILTERS = ("Tous", *CATEGORIES)
# D-050 : une seule table, quatre onglets de statut par priorité. « À vérifier » = lignes non
# confirmées qui ne partiront pas sans confirmation (BLOCKED, REVIEW, ou OK flaguée par un
# événement) ; « OK » = OK non confirmées sans événement ; « Confirmées » ; « Toutes ».
VERIFY, OK_TAB, CONFIRMED_TAB, ALL_TAB = "À vérifier", "OK", "Confirmées", "Toutes"
STATUS_TABS = (VERIFY, OK_TAB, CONFIRMED_TAB, ALL_TAB)
TABLE_HEIGHT = 600
FALLBACK_WEEKS_DEFAULT = 4


def needs_check(base: RecommendationLine) -> bool:
    """Ligne qui ne partira pas sans confirmation : REVIEW, BLOCKED ou flaguée par un
    événement (lancement, demande boutique, promo…)."""
    return base.status is not Status.OK or bool(event_tags(base))


def verify_rank(base: RecommendationLine) -> int:
    """Ordre de l'onglet « À vérifier » : BLOCKED, puis REVIEW, puis flaguées par un événement."""
    if base.status is Status.BLOCKED:
        return 0
    return 1 if base.status is Status.REVIEW else 2


def verify_order(
    lines: list[RecommendationLine], base: dict[str, RecommendationLine]
) -> list[RecommendationLine]:
    """Tri de l'onglet « À vérifier » : BLOCKED → REVIEW → événements, puis Qty proposée
    décroissante (statut et qty du calcul : une ligne ne bouge pas pendant la saisie, D-039)."""
    return sorted(lines, key=lambda x: (verify_rank(base[x.sku]), -base[x.sku].qty, x.sku))


def _tiles(res, pool: list[RecommendationLine]) -> None:  # noqa: ANN001
    """Unités, palettes estimées, et lignes à vérifier (BLOCKED · REVIEW · événement).
    Les SKU inactifs masqués (D-049) ne comptent pas (`pool` = lignes visibles)."""
    s, p = res.summary, res.pallets
    base = {x.sku: x for x in res.base_lines}
    cols = st.columns(3)
    cols[0].metric("Unités", fmt_int(s.units_total))
    cols[1].metric("Palettes estimées", fmt_1(p.total_pallets))
    cols[1].caption(f"VL {fmt_1(p.pallets[VL])} · OL {fmt_1(p.pallets[OL])} · "
                    f"Mach. {fmt_1(p.pallets[MACHINES])} · Autres {fmt_1(p.pallets[OTHERS])}")  # fmt: skip
    counts = [0, 0, 0]
    for x in pool:
        b = base[x.sku]
        if not is_confirmed(x.sku) and needs_check(b):
            counts[verify_rank(b)] += 1
    cols[2].metric("À vérifier", fmt_int(sum(counts)))
    cols[2].caption(f"{STATUS_MD['BLOCKED']} {counts[0]} · {STATUS_MD['REVIEW']} {counts[1]} · "
                    f"événement {counts[2]}")  # fmt: skip


def _status_match(x: RecommendationLine, base: RecommendationLine, status: str) -> bool:
    if status == ALL_TAB:
        return True
    if status == CONFIRMED_TAB:
        return is_confirmed(x.sku)
    if is_confirmed(x.sku):
        return False
    return needs_check(base) if status == VERIFY else not needs_check(base)


def _matches(x: RecommendationLine, base: RecommendationLine, cat: str, status: str,
             qty_only: bool, mod_only: bool, search: str, mapping: dict[str, str],
             event: str | int | None = None) -> bool:  # fmt: skip
    if cat != "Tous" and category_of_line(x, mapping) != cat:
        return False
    if event is not None:
        ids = {t["id"] for t in event_tags(x)}
        if not ids or (event != "all" and event not in ids):
            return False
    if not _status_match(x, base, status):
        return False
    if qty_only and x.qty <= 0:
        return False
    if mod_only and x.qty == base.qty:  # « Modifiées seulement » : Qty finale ≠ Qty proposée
        return False
    return not search or search in x.sku.lower() or search in (x.description or "").lower()


def _event_banner(res) -> str | int | None:  # noqa: ANN001
    """« Équipe : 3 · Personnel : 1 » + une puce par événement, cliquable pour filtrer."""
    others = _others_personal(res)
    if not res.events and not others:
        return None
    if res.events:
        counts: dict[int | None, set[str]] = {}
        for x in res.base_lines:
            for t in event_tags(x):
                counts.setdefault(t["id"], set()).add(x.sku)
        team = sum(e.scope is EventScope.TEAM for e in res.events)
        labels: dict[str | int, str] = {"all": f"Tous ({len(set().union(*counts.values()))} SKU)"}
        for e in res.events:
            n = len(counts.get(e.id, ()))
            labels[e.id or 0] = f"{EVENT_DOT[e.scope.value]} {e.title} ({n} SKU)"
        st.markdown(f"**Événements actifs** · Équipe : {team} · Personnel : "
                    f"{len(res.events) - team} — clic sur un événement pour filtrer la table")  # fmt: skip
        choice = st.pills("Événements", list(labels), key="f_event", format_func=labels.get,
                          label_visibility="collapsed")  # fmt: skip
    else:
        choice = None
    if others:
        listed = ", ".join(f"« {e.title} » ({e.planner})" for e in others)
        st.info(f"Événements personnels d'autres planners pour cette boutique, non appliqués : "
                f"{listed}. Ouvre « Événements SKU » → « Reprendre » pour les appliquer.",
                icon=":material/visibility:")  # fmt: skip
    return choice


def _others_personal(res) -> list:  # noqa: ANN001
    """Personnels d'un collègue, actifs pour cette commande, mais pas appliqués (lecture seule)."""
    p = res.params
    personal = [e for e in all_events() if e.scope is EventScope.PERSONAL and e.id is not None]
    active = select_for_order(personal, boutique=p.boutique, dc=res.dc, delivery=p.delivery_date,
                              planner="", taken_over={e.id for e in personal})  # fmt: skip
    applied = {e.id for e in res.events}
    return [e for e in active if e.id not in applied]


def _filters(
    lines: list[RecommendationLine], base: dict[str, RecommendationLine], mapping: dict[str, str]
) -> tuple[str, str, bool, bool, str]:
    """Onglets de statut (D-050), puis filtres secondaires : catégorie, Qty > 0, Modifiées
    seulement, recherche. Aucun recalcul."""
    by_cat = {c: 0 for c in CATEGORY_FILTERS}
    by_status = {s: 0 for s in STATUS_TABS}
    for x in lines:
        by_cat["Tous"] += 1
        by_cat[category_of_line(x, mapping)] += 1
        for s in STATUS_TABS:
            by_status[s] += _status_match(x, base[x.sku], s)
    status = st.segmented_control("Statut", STATUS_TABS, default=VERIFY, key="f_status",
                                  format_func=lambda c: f"{c} ({by_status[c]})",
                                  label_visibility="collapsed") or VERIFY  # fmt: skip
    c1, c2, c3, c4 = st.columns([4.2, 1, 1.2, 2.2], vertical_alignment="bottom")
    cat = c1.segmented_control("Catégorie", CATEGORY_FILTERS, default="Tous", key="f_cat",
                               format_func=lambda c: f"{c} ({by_cat[c]})",
                               label_visibility="collapsed") or "Tous"  # fmt: skip
    qty_only = c2.checkbox("Qty > 0 seulement", key="f_qty")
    mod_only = c3.checkbox("Modifiées seulement", key="f_mod",
                           help="Qty finale différente de la Qty proposée, quel que soit le statut")  # fmt: skip
    search = c4.text_input("Recherche", placeholder="Rechercher un SKU ou une description",
                           key="f_search", label_visibility="collapsed").strip().lower()  # fmt: skip
    return cat, status, qty_only, mod_only, search


def _bulk_buttons(lines: list[RecommendationLine], shown: list[RecommendationLine],
                  base: dict[str, RecommendationLine], status: str) -> None:  # fmt: skip
    """Un bouton de lot par onglet (D-039, D-050) : « Accepter toutes les lignes OK » sur
    l'onglet OK, « Confirmer les lignes affichées » sur À vérifier et Toutes, rien sur
    Confirmées. Les lignes à stock DC à 0 sont toujours exclues du lot (D-048)."""
    c1, _ = st.columns([1.8, 4.6])
    if status == OK_TAB:
        if c1.button("Accepter toutes les lignes OK", icon=":material/done_all:", key="accept_ok",
                     width="stretch"):  # fmt: skip
            # Les OK flaguées par un événement restent dans « À vérifier » (D-050).
            set_confirmed([x.sku for x in lines if not needs_check(base[x.sku])], True, bulk=True)
            st.rerun()
    elif status != CONFIRMED_TAB:
        todo = [x.sku for x in shown if not is_confirmed(x.sku)]
        if c1.button(f"Confirmer les lignes affichées ({len(todo)})", icon=":material/checklist:",
                     key="confirm_shown", disabled=not todo, width="stretch"):  # fmt: skip
            set_confirmed(todo, True, bulk=True)
            st.rerun()


DC_NOTE = "Confirmé malgré DC à 0"


def _final_qty(sku: str, base_qty: int) -> int:
    ov = ss.overrides.get(sku)
    return int(ov.qty_after) if ov else base_qty


def set_confirmed(skus: list[str], on: bool, *, bulk: bool = False) -> list[str]:
    """Confirme (ou remet à traiter) des lignes. Une ligne bloquée par un événement voit aussi
    son blocage confirmé ; tout ce qui touche un événement est tracé (D-031).

    D-048 : une ligne dont la Qty finale > 0 et le stock DC est à 0 (ou insuffisant) n'est pas
    confirmée au premier clic : elle passe en attente (orange, « confirmer quand même ? ») et
    un second clic la confirme, tracé « Confirmé malgré DC à 0 ». Un lot (`bulk` : « Accepter
    toutes les lignes OK », « Confirmer les lignes affichées », « Tout confirmer ») les exclut
    toujours : elles se confirment une par une. Renvoie les SKU mis en attente.
    """
    base = {x.sku: x for x in ss.base.base_lines} if ss.base else {}
    traced, blocks, forced, held = [], [], [], []
    for sku in skus:
        b = base.get(sku)
        short = b is not None and dc_short(b.model_copy(update={"qty": _final_qty(sku, b.qty)}))
        if on and short and (bulk or sku not in ss.dc_warn):
            ss.dc_warn.add(sku)
            held.append(sku)
            continue
        if on:
            ss.seen.add(sku)
            ss.dc_warn.discard(sku)
            if short:
                ss.dc_forced.add(sku)
                forced.append(sku)
        else:
            ss.seen.discard(sku)
            ss.dc_warn.discard(sku)
            ss.dc_forced.discard(sku)
        if b is not None and blocked_by_event(b):
            changed = (sku in ss.confirmed_blocks) != on
            ss.confirmed_blocks = (
                (ss.confirmed_blocks | {sku}) if on else (ss.confirmed_blocks - {sku})
            )
            if changed:
                blocks.append(sku)
        elif b is not None and event_tags(b):
            traced.append(sku)
    if traced:
        _trace("seen", traced, "" if on else "retiré")
    if blocks:
        _trace("confirm_block", blocks, "" if on else "retiré")
    if forced:
        _trace("confirm_dc0", forced, DC_NOTE)
    if held:
        ss.round_notes = [*(ss.get("round_notes") or []), dc_hold_note(held)]
    ss.nonce += 1
    return held


def dc_hold_note(held: list[str]) -> str:
    """Message après un lot : les lignes DC à 0 sont exclues, à confirmer une par une."""
    return (f"{len(held)} ligne(s) avec stock DC à 0 ou insuffisant, exclue(s) du lot : "
            f"{', '.join(held)}. Coche « Confirmé » une seconde fois sur chacune pour confirmer "
            f"quand même.")  # fmt: skip


def _process_edits(
    edited: pd.DataFrame, lines: dict[str, RecommendationLine], base: dict[str, RecommendationLine]
) -> bool:
    """Grille → actions. True = rerun.

    - Qty finale : modification appliquée tout de suite (D-038), le statut ne change pas (D-039).
      La grille n'est pas recréée : la ligne reste à sa place et le tri choisi est gardé.
    - Confirmé : action distincte ; la ligne passe en « Confirmé » ou revient « à traiter ».
    - Détail : ouvre le panneau « Pourquoi ».
    """
    rows = edited.to_dict("records")
    checked = [r["SKU"] for r in rows if r.get("Détail")]
    new_why = next((s for s in checked if s != ss.why_sku), None)
    rerun = False
    if new_why:
        ss.why_sku, rerun = new_why, True
    elif ss.why_sku in lines and ss.why_sku not in checked:
        ss.why_sku, rerun = None, True
    confirm = [r["SKU"] for r in rows if bool(r["Confirmé"]) and not is_confirmed(r["SKU"])]
    unconfirm = [r["SKU"] for r in rows if not bool(r["Confirmé"]) and is_confirmed(r["SKU"])]
    if confirm or unconfirm:
        set_confirmed(confirm, True)
        set_confirmed(unconfirm, False)
        rerun = True
    new, changed, notes = dict(ss.overrides), False, []
    for row in rows:
        sku, qty = row["SKU"], row["Qty finale"]
        old = ss.overrides.get(sku)
        if pd.isna(qty) or int(qty) == lines[sku].qty or (old and old.qty_after == int(qty)):
            continue
        changed, qty = True, int(qty)
        if qty == base[sku].qty:
            new.pop(sku, None)
        else:
            new[sku] = make_override(base[sku], qty, old.reason if old else "")
            notes.append(rounding_note(base[sku], qty))
    if changed:
        ss.round_notes = [n for n in notes if n]
        # Saisie arrondie : la cellule doit afficher la qty arrondie, la grille est recréée.
        _apply_overrides(new, reset_grid=bool(ss.round_notes))
    return rerun


def _apply_overrides(new: dict, reset_grid: bool = True) -> None:
    data = day_data()
    try:
        with_overrides(ss.base, new.values(), data.masters.products)
    except OverrideError as exc:
        st.error(str(exc))
        return
    ss.overrides, ss.saved_run_id = new, None
    if reset_grid:
        ss.nonce += 1
    st.rerun()


def _close_why() -> None:
    ss.why_sku = None
    ss.nonce += 1


def _toggle_seen(sku: str) -> None:
    set_confirmed([sku], not is_confirmed(sku))


def _toggle_block(sku: str) -> None:
    confirmed = sku not in ss.confirmed_blocks
    ss.confirmed_blocks = (
        (ss.confirmed_blocks | {sku}) if confirmed else (ss.confirmed_blocks - {sku})
    )
    _trace("confirm_block", [sku], "" if confirmed else "retiré")


def _trace(kind: str, skus: list[str], note: str = "") -> None:
    from app.ui.gate import trace

    p = ss.base.params
    trace(p.boutique, p.delivery_date, kind, skus, note)


def _why_panel(res, sku: str, mapping: dict[str, str]) -> None:  # noqa: ANN001
    line = next((x for x in res.lines if x.sku == sku), None)
    if line is None:
        return
    e, inp = line.explanation, line.explanation["inputs"]
    t1, t2 = st.columns([5, 1], vertical_alignment="top")
    t1.markdown(
        f"**Pourquoi cette quantité ?**  \n`{sku}` {line.description or 'sans description'}"
    )
    t2.button("", icon=":material/close:", key="why_close", on_click=_close_why, help="Fermer")
    base_line = next((x for x in res.base_lines if x.sku == sku), line)
    shown = shown_status(sku, base_line.status)
    status = (CONFIRMED_MD if shown == "Confirmé" else STATUS_MD[shown]) + (
        f" {MODIFIED_MD}" if line.qty != base_line.qty else ""
    )
    st.caption(f"{status} · {category_of_line(line, mapping)} · source : "
               f"{SOURCE_LABEL[line.source.value]} · multiple {fmt_int(e['multiple'])}")  # fmt: skip
    st.markdown("\n".join(f"{i}. {step}" for i, step in enumerate(explain_steps(line), start=1)))
    dc = inp["dc_available"]
    st.markdown(f"Expected après commande : **{fmt_int(inp['expected_total'] + line.qty)}** "
                f"(Expected du jour {fmt_int(inp['expected_total'])} + Qty finale {fmt_int(line.qty)})")  # fmt: skip
    incoming = incoming_of(line)
    if incoming is not None:
        st.markdown(f"Available (en boutique) : **{fmt_int(inp.get('available_total'))}** · "
                    + (f"+ {fmt_int(incoming)} en route" if incoming >= 0
                       else f"− {fmt_int(-incoming)} en attente de sortie"))  # fmt: skip
    target = cover_target(line)
    st.markdown(f"Couverture : actuelle {fmt_1(cover_after(line, 0))} j → après commande "
                f"**{fmt_1(cover_after(line))} j**"
                + (f" · cible {fmt_1(target)} j" if target is not None else ""))  # fmt: skip
    st.markdown(f"Stock DC : **{fmt_int(dc) if dc is not None else 'absent'}** · "
                f"stock projeté à la livraison : **{fmt_int(e['projected_stock_at_delivery'])}**")  # fmt: skip
    if line.reasons:
        st.markdown("\n".join(f"- {r}" for r in line.reasons))
    if ev := event_cell(base_line):  # D-046 : plus de colonne Événement dans la grille
        st.markdown(f"Événement(s) : {ev}")
    if blocked_by_event(base_line):
        done = sku in ss.confirmed_blocks
        st.button("Retirer la confirmation du blocage" if done else "Confirmer le blocage (qty 0)",
                  icon=":material/undo:" if done else ":material/block:", key="why_block",
                  on_click=_toggle_block, args=(sku,), width="stretch",
                  help="Bloqué par un événement : confirmer (tracé), ou modifier la quantité "
                       "AVEC une raison pour l'envoyer quand même.")  # fmt: skip
    st.button("Retirer la confirmation" if is_confirmed(sku) else "Confirmer la ligne",
              icon=":material/undo:" if is_confirmed(sku) else ":material/check:",
              key="why_seen", on_click=_toggle_seen, args=(sku,), width="stretch")  # fmt: skip
    st.markdown("**Modifier la quantité**")
    qty = st.number_input("Qty finale", min_value=0, step=int(e["multiple"] or 1),
                          value=int(line.qty), key=f"dlg_qty_{sku}")  # fmt: skip
    old = ss.overrides.get(sku)
    reason = st.text_input("Raison (facultatif)", value=old.reason if old else "",
                           key=f"dlg_reason_{sku}")  # fmt: skip
    if st.button("Enregistrer", type="primary", key="dlg_save", width="stretch"):
        new = dict(ss.overrides)
        if qty == base_line.qty:
            new.pop(sku, None)
        else:
            new[sku] = make_override(base_line, int(qty), reason)
            ss.round_notes = [n for n in [rounding_note(base_line, int(qty))] if n]
        _apply_overrides(new)


def render_order(res, data) -> None:  # noqa: ANN001 - PlanningResult (avec modifications), DayData
    """Table de commande pleine page de la boutique affichée."""
    p = res.params
    names, mapping = data.masters.boutique_names, data.categories
    st.markdown(
        f"**{boutique_label(p.boutique, names)}** · DC {res.dc} · livraison **{fmt_date(p.delivery_date)}** · "
        f"cover {fmt_1(p.cover_days)} j"
        + (f" (café {fmt_1(p.coffee_cover_days)} j)" if p.coffee_cover_days else "")
        + f" · historique {res.history_days} j · {MODE_LABEL[res.mode.value]}"
    )
    if not author():
        st.caption(":material/info: Choisis ton prénom (Planner) pour appliquer aussi tes "
                   "événements personnels.")  # fmt: skip
    # D-049 : les anciens SKU (tout à zéro, sans vente ni stock DC) sont masqués et ne comptent
    # pas dans les tuiles ; « Afficher les SKU inactifs (N) » sous la table les montre.
    inactive = {x.sku for x in res.lines if is_inactive_line(x)}
    pool = res.lines if ss.show_inactive else [x for x in res.lines if x.sku not in inactive]
    _tiles(res, pool)
    for w in res.rule_warnings:
        st.warning(w)

    event = _event_banner(res)
    base = {x.sku: x for x in res.base_lines}
    cat, status, qty_only, mod_only, search = _filters(pool, base, mapping)
    picked = [x for x in pool
              if _matches(x, base[x.sku], cat, status, qty_only, mod_only, search, mapping, event)]  # fmt: skip
    shown = verify_order(picked, base) if status == VERIFY else default_order(picked, base)
    _bulk_buttons(pool, shown, base, status)
    for note in ss.pop("round_notes", None) or []:
        st.caption(f":orange[:material/warning:] {note}")
    side = bool(ss.why_sku)
    table, panel = st.columns([2.8, 1.2]) if side else (st.container(), None)
    with table:
        df = order_frame(shown, base, mapping)
        # Même grille tant que les lignes affichées ne changent pas : taper une qty ne recrée
        # pas la table, la ligne reste en place et le tri choisi au clic est gardé (D-039).
        rows_id = hash(tuple(x.sku for x in shown))
        key = (
            f"grid_{p.boutique}_{cat}_{status}_{qty_only}_{mod_only}_{search}_{event}_"
            f"{ss.nonce}_{rows_id}"
        )
        targets = {x.sku: cover_target(x) for x in shown}
        edited = st.data_editor(style_order_frame(df, targets), key=key, hide_index=True, width="stretch", height=TABLE_HEIGHT, placeholder="—",
                                column_config=ORDER_COLUMN_CONFIG,
                                disabled=[c for c in df.columns if c not in EDITABLE_COLUMNS])  # fmt: skip
        done = sum(is_confirmed(x.sku) for x in pool)
        st.caption(f"{len(shown)} ligne(s) affichée(s) sur {len(pool)} · lignes confirmées : "
                   f"{done} / {len(pool)} · clic sur un en-tête pour trier · case Détail pour le « Pourquoi »")  # fmt: skip
        st.checkbox(f"Afficher les SKU inactifs ({len(inactive)})", key="show_inactive",
                    help="Anciens SKU : Expected, Available, Incoming, ventes et stock DC à 0. "
                    "Calculés quand même, masqués de la table et des tuiles (D-049).")  # fmt: skip
    if _process_edits(edited, {x.sku: x for x in shown}, base):
        ss.nonce += 1
        st.rerun()
    if panel is not None:
        with panel, st.container(border=True, height=TABLE_HEIGHT + 38, key="why_panel"):
            _why_panel(res, ss.why_sku, mapping)
