"""Interface Streamlit du Supply Planning Copilot.

Lancer :  streamlit run app/ui/main.py

Toutes les quantités viennent du moteur Python (app/engines). L'interface ne calcule
rien elle-même : elle collecte les paramètres, affiche, enregistre et exporte.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from app.domain.models import (  # noqa: E402
    ForecastMode,
    Override,
    PlanningParameters,
    RecommendationLine,
)
from app.engines.compare import compare_orders  # noqa: E402
from app.engines.export import lt_content, lt_filename, recap_csv  # noqa: E402
from app.engines.overrides import OverrideError  # noqa: E402
from app.ingestion.masters import boutiques_available, build_inputs_for_boutique  # noqa: E402
from app.ingestion.powerbi import check_freshness  # noqa: E402
from app.ingestion.tables import ValidationReport  # noqa: E402
from app.services.planning import PlanningResult, run_planning, with_overrides  # noqa: E402
from app.storage import db  # noqa: E402
from app.ui.data_sources import (  # noqa: E402
    LoadedData,
    load_demo,
    load_uploads,
    save_master_uploads,
)

st.set_page_config(page_title="Supply Planning Copilot", page_icon="📦", layout="wide")

STATUS_LABEL = {"OK": "🟢 OK", "REVIEW": "🟠 REVIEW", "BLOCKED": "🔴 BLOCKED"}
SOURCE_LABEL = {
    "forecast": "Prévision",
    "allocation": "Allocation",
    "launch": "Lancement",
    "target_stock": "Stock cible",
    "override": "Override",
}
MULTIPLE_SOURCE = {
    "multiple_list": "Multiple list",
    "inferred_family": "inféré depuis la famille (VER/ORI)",
    "excel_default_1": "absent → 1 (comme Excel)",
    "missing_default_1": "absent → 1 (non fiable)",
    "excel_zero_multiple": "0 dans la Multiple list",
}

ss = st.session_state
ss.setdefault("data", None)
ss.setdefault("base", None)  # PlanningResult sans override
ss.setdefault("overrides", {})  # sku -> Override
ss.setdefault("saved_run_id", None)
ss.setdefault("inputs", None)


def fmt(x: float | None, digits: int = 0) -> str:
    if x is None:
        return "—"
    return f"{x:,.{digits}f}".replace(",", " ")


def num(x: float | None) -> float:
    """Valeur numérique pour un tableau : vide (NaN) plutôt que « None »."""
    return float("nan") if x is None else x


def reset_run() -> None:
    ss.base, ss.overrides, ss.saved_run_id, ss.inputs = None, {}, None, None


def current() -> PlanningResult | None:
    if ss.base is None:
        return None
    return with_overrides(ss.base, ss.overrides.values(), ss.data.masters.products)


# --- Barre latérale : qui, quelles données --------------------------------------------

with st.sidebar:
    st.header("Supply Planning Copilot")
    author = st.text_input(
        "Ton nom (planner)", key="author", help="Obligatoire pour modifier et enregistrer"
    )
    st.divider()
    st.subheader("Données")
    source = st.radio(
        "Source", ["Démo B80 (données de test)", "Mes fichiers"], key="source", index=0
    )
    if source.startswith("Démo"):
        if st.button("Charger la démo", type="primary", key="load_demo"):
            ss.data = load_demo()
            reset_run()
    else:
        stock_up = st.file_uploader("Stock Situation (Power BI)", type=["csv", "xlsx"])
        moves_up = st.file_uploader("Stock Movements (Power BI)", type=["csv", "xlsx"])
        masters_up = st.file_uploader(
            "Tables maîtres (master_*.csv), si nouvelles",
            type=["csv"],
            accept_multiple_files=True,
            help="Produites par tools/extract_masters.py. Gardées dans data/masters/.",
        )
        with st.expander("Allocations, lancements, stock cible (facultatif)"):
            alloc_up = st.file_uploader("allocations.xlsx", type=["csv", "xlsx"])
            launch_up = st.file_uploader("launches.xlsx", type=["csv", "xlsx"])
            target_up = st.file_uploader("target_stock.xlsx", type=["csv", "xlsx"])
        if st.button("Charger mes fichiers", type="primary", key="load_files"):
            try:
                if masters_up:
                    save_master_uploads(masters_up)
                if not (stock_up and moves_up):
                    st.error("Il faut au moins la Stock Situation et les Stock Movements.")
                else:
                    ss.data = load_uploads(
                        stock_up, moves_up, allocations_file=alloc_up,
                        launches_file=launch_up, targets_file=target_up,
                    )  # fmt: skip
                    reset_run()
            except (ValueError, OSError) as exc:
                st.error(f"Chargement impossible : {exc}")
    if ss.data is not None:
        st.success(f"{ss.data.label} : {len(ss.data.stock)} lignes de stock, "
                   f"{len(ss.data.movements)} mouvements")  # fmt: skip
        if ss.data.has_errors:
            st.error("Des erreurs bloquantes ont été trouvées : voir l'onglet Qualité des données.")

st.title("Commande boutique")
if ss.data is None:
    st.info(
        "Commence par charger des données dans la barre de gauche "
        "(la démo B80 permet de tout essayer)."
    )
    st.stop()

data: LoadedData = ss.data
tab_params, tab_valid, tab_summary, tab_history, tab_quality = st.tabs(
    ["1. Paramètres", "2. Validation", "3. Résumé & export", "4. Historique", "Qualité des données"]
)

# --- 1. Paramètres -------------------------------------------------------------------------

with tab_params:
    boutiques = boutiques_available(data.stock, data.masters.dc_mapping)
    if not boutiques:
        st.error("Aucune boutique commune entre la Stock Situation et le DC Mapping.")
        st.stop()
    is_demo = data.label.startswith("Démo")
    c1, c2, c3 = st.columns(3)
    boutique = c1.selectbox("Boutique", boutiques, key="boutique")
    run_date = c2.date_input(
        "Date du run", value=date(2026, 10, 5) if is_demo else date.today(), key="run_date"
    )
    delivery_date = c3.date_input("Date de livraison", value=None, key="delivery_date")
    c4, c5, c6 = st.columns(3)
    cover = c4.number_input(
        "Jours de couverture", min_value=0.5, max_value=60.0, step=0.5, value=None,
        key="cover", help="Saisis la valeur du jour : il n'y a pas de valeur par défaut.",
    )  # fmt: skip
    use_coffee = c5.checkbox("Cover différent pour les cafés (type C)", key="use_coffee")
    coffee = (
        c5.number_input(
            "Jours de couverture café",
            min_value=0.5,
            max_value=60.0,
            step=0.5,
            value=None,
            key="coffee",
        )  # fmt: skip
        if use_coffee
        else None
    )
    mode_label = c6.radio(
        "Mode",
        ["Safe (recommandé)", "Parité Excel"],
        key="mode",
        help="Safe : un café sans multiple reçoit 240 (VER) / 800 (ORI) au lieu de 1. "
        "Parité Excel : exactement comme le calculateur.",
    )
    mode = ForecastMode.SAFE if mode_label.startswith("Safe") else ForecastMode.EXCEL_PARITY
    with st.expander("Avancé : codes de mouvement inclus dans les ventes"):
        all_codes = sorted({m.movement_code for m in data.movements if m.location == boutique})
        codes = st.multiselect(
            "Codes inclus (par défaut : tous, ventes ET déstockages)", all_codes,
            default=all_codes, key="codes",
        )  # fmt: skip

    if st.button("Calculer la commande", type="primary", key="compute"):
        problems = []
        if delivery_date is None:
            problems.append("la date de livraison")
        if cover is None:
            problems.append("les jours de couverture")
        if use_coffee and coffee is None:
            problems.append("les jours de couverture café")
        if problems:
            st.error("Il manque : " + ", ".join(problems) + ".")
        else:
            try:
                params = PlanningParameters(
                    boutique=boutique, run_date=run_date, delivery_date=delivery_date,
                    cover_days=cover, coffee_cover_days=coffee,
                )  # fmt: skip
                inputs, dc = build_inputs_for_boutique(
                    boutique, data.stock, data.movements, data.masters.products,
                    data.masters.conversions, data.masters.dc_mapping,
                    included_movement_codes=None if set(codes) == set(all_codes) else codes,
                    sources=data.source_hashes,
                )  # fmt: skip
                ss.base = run_planning(
                    inputs, params, mode, dc=dc,
                    portfolio=data.masters.portfolio.get(boutique, {}),
                    business_inputs=data.business,
                )  # fmt: skip
                ss.inputs = inputs
                ss.overrides, ss.saved_run_id = {}, None
                fresh = [r for r in data.reports if r.source == "Fraîcheur"]
                for r in fresh:
                    data.reports.remove(r)
                fr = ValidationReport(source="Fraîcheur")
                check_freshness(fr, run_date, movements=[
                    m for m in data.movements if m.location == boutique])  # fmt: skip
                data.reports.append(fr)
                st.success("Commande calculée : va dans l'onglet 2. Validation.")
            except ValidationError as exc:
                st.error("Paramètres invalides : " + "; ".join(e["msg"] for e in exc.errors()))
            except ValueError as exc:
                st.error(f"Calcul impossible : {exc}")

    res = current()
    if res is not None:
        p = res.params
        st.caption(
            f"Dernier calcul : {p.boutique} (DC {res.dc}) · run {p.run_date} · livraison "
            f"{p.delivery_date} · cover {p.cover_days:g} j"
            + (f" · café {p.coffee_cover_days:g} j" if p.coffee_cover_days else "")
            + f" · mode {res.mode.value} · historique {ss.inputs.sales.history_days} jours"
        )

res = current()

# --- 2. Validation ----------------------------------------------------------------------------


def why(line: RecommendationLine, history_days: int) -> None:
    e = line.explanation
    inp = e["inputs"]
    st.markdown(f"#### {line.sku} — {line.description or 'sans description'}")
    st.markdown(
        f"{STATUS_LABEL[line.status.value]} · type `{line.product_type or '?'}` · "
        f"origine : **{SOURCE_LABEL[line.source.value]}**"
    )
    rows = [
        ("Cover appliqué", f"{e['cover_applied']:g} j ({e['cover_source']})"),
        (f"Ventes ({history_days} j)", fmt(inp["sales_total"])),
        ("Expected", fmt(inp["expected_total"])),
    ]
    if inp["sales_from_old_skus"]:
        for old, s in inp["sales_from_old_skus"].items():
            rows.append((f"dont ancien SKU {old}", f"ventes {fmt(s)}, Expected "
                         f"{fmt(inp['expected_from_old_skus'][old])}"))  # fmt: skip
    if e["raw_need"] is None:
        rows.append(("Calcul", "non effectué (règle de blocage)"))
    else:
        rows += [
            (f"Besoin = cover × ventes / {history_days} − Expected", fmt(e["raw_need"], 2)),
            (
                f"Multiple ({MULTIPLE_SOURCE.get(e['multiple_source'], e['multiple_source'])})",
                fmt(e["multiple"]),
            ),  # fmt: skip
            (
                "Quantité prévision",
                fmt(line.forecast_qty if line.forecast_qty is not None else e["qty"]),
            ),  # fmt: skip
        ]
    rows.append(("**Quantité finale**", f"**{fmt(line.qty)}**"))
    rows += [
        (
            "Couverture actuelle → après",
            f"{fmt(e['current_cover_days'], 1)} j → {fmt(e['post_cover_days'], 1)} j",
        ),  # fmt: skip
        (
            f"Stock projeté à la livraison ({e['days_until_delivery']} j)",
            fmt(e["projected_stock_at_delivery"]),
        ),  # fmt: skip
        (
            "Stock DC disponible",
            fmt(inp["dc_available"]) if inp["dc_available"] is not None else "SKU absent du DC",
        ),  # fmt: skip
    ]
    st.table(pd.DataFrame(rows, columns=["Étape", "Valeur"]).set_index("Étape"))
    if line.reasons:
        st.markdown("**Signalements et décisions**")
        for r in line.reasons:
            st.markdown(f"- {r}")
    with st.expander("Trace complète (JSON)"):
        st.json(e)


with tab_valid:
    if res is None:
        st.info("Calcule d'abord une commande dans l'onglet 1.")
    else:
        f1, f2, f3, f4 = st.columns([2, 1, 1, 2])
        statuses = f1.multiselect("Statut", ["OK", "REVIEW", "BLOCKED"],
                                  default=["OK", "REVIEW", "BLOCKED"], key="f_status")  # fmt: skip
        only_qty = f2.checkbox("Qty > 0", key="f_qty")
        only_conv = f3.checkbox("Conversions", key="f_conv")
        search = f4.text_input("Recherche SKU / description", key="f_search").strip().lower()
        base_qty = {x.sku: x.qty for x in ss.base.lines}
        shown = [
            x for x in res.lines
            if x.status.value in statuses
            and (not only_qty or x.qty > 0)
            and (not only_conv or x.explanation["inputs"]["sales_from_old_skus"]
                 or "old_sku_blocked" in x.explanation["rules_triggered"]
                 or any("Conversion" in r for r in x.reasons))
            and (not search or search in x.sku.lower() or search in (x.description or "").lower())
        ]  # fmt: skip
        df = pd.DataFrame(
            [
                {
                    "SKU": x.sku,
                    "Description": x.description or "",
                    "Type": x.product_type or "",
                    "Statut": STATUS_LABEL[x.status.value],
                    "Origine": SOURCE_LABEL[x.source.value],
                    "Ventes": x.explanation["inputs"]["sales_total"],
                    "Expected": x.explanation["inputs"]["expected_total"],
                    "Couv. actuelle": num(x.explanation["current_cover_days"]),
                    "Multiple": x.explanation["multiple"],
                    "Qty proposée": base_qty[x.sku],
                    "Qty finale": x.qty,
                    "Raison de la modification": ss.overrides[x.sku].reason
                    if x.sku in ss.overrides else "",
                    "Couv. après": num(x.explanation["post_cover_days"]),
                    "Signalements": " | ".join(x.reasons),
                }
                for x in shown
            ]
        )  # fmt: skip
        st.caption(
            f"{len(shown)} ligne(s) sur {len(res.lines)}. Modifie « Qty finale » et donne une "
            "raison, puis clique sur « Appliquer ». Applique avant de changer de filtre."
        )
        edited = st.data_editor(
            df,
            key=f"editor_{len(ss.overrides)}_{'-'.join(statuses)}_{only_qty}_{only_conv}_{search}",
            hide_index=True,
            width="stretch",
            disabled=[
                c for c in df.columns if c not in ("Qty finale", "Raison de la modification")
            ],
            column_config={
                "Qty finale": st.column_config.NumberColumn(min_value=0, step=1, format="%d"),
                "Ventes": st.column_config.NumberColumn(format="%.0f"),
                "Expected": st.column_config.NumberColumn(format="%.0f"),
                "Couv. actuelle": st.column_config.NumberColumn(format="%.1f"),
                "Couv. après": st.column_config.NumberColumn(format="%.1f"),
                "Signalements": st.column_config.TextColumn(width="large"),
            },
        )
        if st.button("Appliquer les modifications", type="primary", key="apply"):
            errors, new = [], dict(ss.overrides)
            for row in edited.to_dict("records"):
                sku, after = row["SKU"], int(row["Qty finale"] or 0)
                reason = str(row["Raison de la modification"] or "").strip()
                if after == base_qty[sku]:
                    new.pop(sku, None)
                    continue
                if not author.strip():
                    errors.append("indique ton nom dans la barre de gauche")
                    break
                if len(reason) < 3:
                    errors.append(f"{sku} : raison obligatoire")
                    continue
                new[sku] = Override(sku=sku, qty_before=base_qty[sku], qty_after=after,
                                    reason=reason, author=author, timestamp=datetime.now())  # fmt: skip
            if errors:
                st.error("Modifications non appliquées : " + "; ".join(errors))
            else:
                try:
                    with_overrides(ss.base, new.values(), data.masters.products)
                    ss.overrides, ss.saved_run_id = new, None
                    st.rerun()
                except OverrideError as exc:
                    st.error(str(exc))
        st.divider()
        st.subheader("Pourquoi cette quantité ?")
        options = [x.sku for x in shown] or [x.sku for x in res.lines]
        pick = st.selectbox(
            "SKU", options, key="why_sku",
            format_func=lambda s: f"{s} — {next((x.description or '' for x in res.lines if x.sku == s), '')}",
        )  # fmt: skip
        if pick:
            why(next(x for x in res.lines if x.sku == pick), ss.inputs.sales.history_days)

# --- 3. Résumé & export --------------------------------------------------------------------------


def save_current(result: PlanningResult) -> int | None:
    if not author.strip():
        st.error("Indique ton nom dans la barre de gauche pour enregistrer.")
        return None
    conn = db.connect()
    try:
        run_id = db.save_run(conn, result, author=author, sources=data.source_hashes,
                             overrides=ss.overrides.values())  # fmt: skip
    finally:
        conn.close()
    ss.saved_run_id = run_id
    return run_id


def on_export(result: PlanningResult, filename: str) -> None:
    run_id = ss.saved_run_id or save_current(result)
    if run_id:
        conn = db.connect()
        try:
            db.mark_exported(conn, run_id, filename)
        finally:
            conn.close()


with tab_summary:
    if res is None:
        st.info("Calcule d'abord une commande dans l'onglet 1.")
    else:
        s = res.summary
        m = st.columns(5)
        m[0].metric("Lignes à commander", s.lines_ordered)
        m[1].metric("Unités", fmt(s.units_total))
        m[2].metric("Palettes estimées", fmt(res.pallets.total_pallets, 2))
        m[3].metric("REVIEW", s.by_status["REVIEW"])
        m[4].metric("BLOCKED", s.by_status["BLOCKED"])
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Palettes par famille**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Famille": f, "Unités": u, "Palettes": round(res.pallets.pallets[f], 2)}
                        for f, u in res.pallets.units.items()
                    ]  # fmt: skip
                ),
                hide_index=True,
                width="stretch",
            )
            if res.pallets.skus_without_pallet_size:
                st.warning("Sans taille de palette (non comptés) : "
                           + ", ".join(res.pallets.skus_without_pallet_size))  # fmt: skip
            st.markdown("**Origine des quantités commandées**")
            st.dataframe(pd.DataFrame([{"Origine": SOURCE_LABEL[k], "Lignes": v}
                                       for k, v in s.by_source.items() if v]),  # fmt: skip
                         hide_index=True, width="stretch")  # fmt: skip
        with c2:
            st.markdown("**Signalements les plus fréquents**")
            st.dataframe(pd.DataFrame(s.reason_counts[:15], columns=["Signalement", "Lignes"]),
                         hide_index=True, width="stretch")  # fmt: skip
            if s.unreliable_ordered:
                st.warning("Quantités commandées non fiables (multiple absent) : "
                           + ", ".join(s.unreliable_ordered))  # fmt: skip
        with st.expander(f"Lignes exclues par la règle « Filter out » ({len(res.excluded)})"):
            st.dataframe(pd.DataFrame(sorted(res.excluded.items()), columns=["SKU", "Raison"]),
                         hide_index=True, width="stretch")  # fmt: skip

        st.divider()
        st.subheader("Export")
        if s.by_status["BLOCKED"] or s.by_status["REVIEW"]:
            st.info(f"{s.by_status['REVIEW']} ligne(s) REVIEW à vérifier avant l'envoi. "
                    "L'export reste possible : c'est toi qui décides.")  # fmt: skip
        name = lt_filename(res.params.boutique, datetime.now())
        e1, e2, e3 = st.columns(3)
        if e1.button("Enregistrer le run", key="save") and save_current(res):
            st.success(f"Run n° {ss.saved_run_id} enregistré.")
        e2.download_button(
            "Télécharger le fichier LT (Nessoft)", data=lt_content(res.lines), file_name=name,
            mime="text/csv", type="primary", key="dl_lt", on_click=on_export, args=(res, name),
        )  # fmt: skip
        e3.download_button(
            "Télécharger le récapitulatif boutique",
            data=recap_csv(
                res.params.boutique, res.params.delivery_date.isoformat(), res.lines, res.pallets
            ),  # fmt: skip
            file_name=f"Recap {name}",
            mime="text/csv",
            key="dl_recap",
        )
        if ss.saved_run_id:
            st.caption(f"Run enregistré : n° {ss.saved_run_id}.")
        st.caption(
            "Le fichier LT est à importer à la main dans Nessoft : l'outil n'y écrit jamais."
        )

# --- 4. Historique ----------------------------------------------------------------------------------

with tab_history:
    conn = db.connect()
    try:
        b = ss.get("boutique")
        runs = db.list_runs(conn, b)
        if not runs:
            st.info(f"Aucun run enregistré pour {b}.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "N°": r["id"],
                            "Créé le": r["created_at"],
                            "Par": r["author"],
                            "Livraison": r["delivery_date"],
                            "Cover": r["cover_days"],
                            "Mode": r["mode"],
                            "Lignes": r["summary"]["lines_ordered"],
                            "Unités": r["summary"]["units_total"],
                            "Exporté": r["lt_filename"] or "",
                        }
                        for r in runs
                    ]  # fmt: skip
                ),
                hide_index=True,
                width="stretch",
            )
            pick = st.selectbox("Comparer la commande actuelle avec le run", [r["id"] for r in runs],
                                key="hist_pick")  # fmt: skip
            if pick and res is not None:
                diff = compare_orders(db.run_quantities(conn, pick),
                                      {x.sku: x.qty for x in res.lines})  # fmt: skip
                st.markdown(f"**{len(diff)} SKU dont la quantité change**")
                st.dataframe(pd.DataFrame(diff), hide_index=True, width="stretch")
            if pick:
                ovs = db.run_overrides(conn, pick)
                if ovs:
                    st.markdown("**Overrides de ce run**")
                    st.dataframe(pd.DataFrame(ovs).drop(columns=["id", "run_id"]),
                                 hide_index=True, width="stretch")  # fmt: skip
    finally:
        conn.close()

# --- Qualité des données -------------------------------------------------------------------------

with tab_quality:
    rows = [row for r in data.reports for row in r.as_rows()]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.success("Aucun problème détecté dans les fichiers.")
    st.caption(
        "ERROR = fichier inutilisable en l'état · WARNING = à vérifier · INFO = pour mémoire"
    )
    if res is not None and res.summary.reason_counts:
        st.markdown("**Anomalies du run** : voir aussi l'onglet 3.")
