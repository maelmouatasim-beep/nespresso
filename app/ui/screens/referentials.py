"""Écran 6 — Référentiels : 3 onglets, Produits, Boutiques et Planners.

Produits : une ligne par SKU (Multiple list, catégorie, conversion, exclusion).
Boutiques : une ligne par boutique (DC, nom, carrier, planner habituel), avec règles et
créneaux au clic et le portfolio en sous-onglet. Tout est modifiable (D-037) ; chaque
onglet garde son propriétaire, sa date de mise à jour et l'historique des modifications.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from app.engines.categories import CATEGORIES
from app.ingestion.boutique_table import ANOMALIES as B_ANOMALIES
from app.ingestion.boutique_table import BOUTIQUE_COLUMNS, CODE, PLANNER, PORTFOLIO, SLOTS
from app.ingestion.product_table import ANOMALIES, CAT, PRODUCT_COLUMNS, SKU, build_product_rows
from app.ingestion.referential_edit import DEFAULT_COLUMNS, table_csv
from app.ingestion.tables import read_raw
from app.storage import events as event_store
from app.ui.common import author, db_conn, fmt_date, reset_all, ss, workspace
from app.ui.workspace import REF_BY_KEY, ROOT, Referential

TEMPLATES = ROOT / "templates"
ANOMALY_FILTER = "Anomalies"
ANOMALY_COL = "⚠"
SLOT_COLUMNS = ["order_day", "shipping_day", "delivery_day", "carrier", "deliveries_per_week",
                "cover_days", "notes"]  # fmt: skip
RULE_COLUMNS = ["rule", "sku", "value", "comment"]


def _need_author() -> bool:
    if author():
        return False
    st.error("Choisis ton prénom (Planner, barre de gauche).")
    return True


def _owner_changed(key: str) -> None:
    workspace().set_meta(key, owner=ss.get(f"owner_{key}", ""))


def _tab_header(key: str) -> None:
    """Propriétaire, date de mise à jour et historique des modifications de l'onglet."""
    ws = workspace()
    meta = ws.meta().get(key, {})
    c1, c2 = st.columns([1.2, 3], vertical_alignment="bottom")
    c1.text_input("Propriétaire", value=meta.get("owner", ""), key=f"owner_{key}",
                  on_change=_owner_changed, args=(key,))  # fmt: skip
    when = meta.get("updated_at")
    c2.markdown(
        f"Mis à jour le **{fmt_date(pd.Timestamp(when[:10]).date()) if when else '—'}**"
        + (f" à {when[11:16]}" if when and len(when) > 11 else "")
        + (f" par {meta['updated_by']}" if meta.get("updated_by") else "")
    )
    history = ws.history(key)
    with st.expander(f"Historique des modifications ({len(history)})"):
        if not history:
            st.caption("Aucune modification enregistrée dans l'outil.")
        for h in history[:200]:
            st.markdown(f"- {h['at'].replace('T', ' ')} · **{h['by']}** · " + " · ".join(h["changes"][:20])
                        + (f" … (+{len(h['changes']) - 20})" if len(h["changes"]) > 20 else ""))  # fmt: skip


# --- Produits --------------------------------------------------------------------------------


def _product_frame() -> pd.DataFrame:
    rows = build_product_rows(workspace().product_sources())
    df = pd.DataFrame(rows, columns=[*PRODUCT_COLUMNS, ANOMALIES], dtype=str)
    df.insert(0, ANOMALY_COL, df[ANOMALIES].map(lambda a: "🟠" if a else ""))
    return df


def _products_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.drop(columns=[ANOMALY_COL]).to_excel(buf, index=False, sheet_name="Produits")
    return buf.getvalue()


def _save_products(rows: list[dict], what: str) -> None:
    errors, warnings = workspace().save_products(rows, author())
    if errors:
        st.error("Rien n'est enregistré : " + " · ".join(errors[:10]))
        return
    reset_all()
    for w in warnings:
        st.warning(w)
    st.success(f"{what} enregistré. La commande sera recalculée.")


def _products_tab() -> None:
    _tab_header("produits")
    ws = workspace()
    if not ws.has_masters():
        st.info("Aucune Multiple list : importe le calculateur ou les référentiels de test.")
        return
    df = _product_frame()
    f1, f2, f3 = st.columns([2, 1.4, 1], vertical_alignment="bottom")
    q = f1.text_input("Rechercher un SKU ou une description", key="prod_q").strip().lower()
    cat = f2.selectbox("Catégorie", ["Toutes", *CATEGORIES], key="prod_cat")
    only = f3.toggle(ANOMALY_FILTER, key="prod_anom", help="Seulement les lignes avec une anomalie")
    mask = pd.Series(True, index=df.index)
    if q:
        mask &= df[SKU].str.lower().str.contains(q, regex=False) | df["Description"].str.lower(
        ).str.contains(q, regex=False)  # fmt: skip
    if cat != "Toutes":
        mask &= df[CAT] == cat
    if only:
        mask &= df[ANOMALIES] != ""
    view = df[mask]
    st.caption(f"{len(view)} / {len(df)} produits · {int((df[ANOMALIES] != '').sum())} avec une "
               "anomalie 🟠 · clic sur une cellule pour la modifier, « + » pour ajouter, case à "
               "gauche puis Suppr pour retirer. Un multiple modifié s'applique à toutes les "
               "lignes du SKU ; une catégorie modifiée reclasse tout son type produit.")  # fmt: skip
    edited = st.data_editor(
        view, num_rows="dynamic", hide_index=True, width="stretch", height=460,
        key=f"prod_grid_{ss.nonce}",
        disabled=[ANOMALY_COL, ANOMALIES],
        column_config={
            ANOMALY_COL: st.column_config.TextColumn(width=36, help="Anomalie"),
            SKU: st.column_config.TextColumn(width=120, required=True),
            CAT: st.column_config.SelectboxColumn(options=list(CATEGORIES), width=170),
            "Exclu": st.column_config.TextColumn(width=90, help="oui = toutes les boutiques, ou codes séparés par ;"),
            ANOMALIES: st.column_config.TextColumn(width=280),
        },
    )  # fmt: skip
    c1, c2 = st.columns([1, 3], vertical_alignment="bottom")
    if c1.button("Enregistrer les produits", type="primary", key="save_products"):
        if _need_author():
            return
        shown = set(view[SKU])
        rest = [r for r in df.drop(columns=[ANOMALY_COL]).to_dict("records") if r[SKU] not in shown]
        _save_products(rest + edited.drop(columns=[ANOMALY_COL]).to_dict("records"), "Produits")
    with st.expander("Import / export Excel"):
        st.download_button("Exporter les produits (Excel)", data=_products_xlsx(df),
                           file_name="produits.xlsx", key="prod_export",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")  # fmt: skip
        up = st.file_uploader("Importer un classeur (mêmes colonnes) : les SKU présents sont "
                              "mis à jour ou ajoutés, les autres ne changent pas",
                              type=["xlsx", "csv"], key=f"prod_imp_{ss.nonce}")  # fmt: skip
        if up is not None and st.button("Importer", key="prod_imp_btn") and not _need_author():
            raw = read_raw(io.BytesIO(up.getvalue()), up.name)
            head = [str(h).strip() for h in raw.rows[0]] if raw.rows else []
            missing = [c for c in (SKU,) if c not in head]
            if missing:
                st.error(
                    f"Colonne manquante : {missing}. Pars de l'export pour avoir les bonnes colonnes."
                )
                return
            current = {r[SKU]: r for r in df.drop(columns=[ANOMALY_COL]).to_dict("records")}
            for values in raw.rows[1:]:
                rec = dict(zip(head, values, strict=False))
                sku = str(rec.get(SKU, "")).strip()
                if sku:
                    current[sku] = {**current.get(sku, {}), **{c: rec[c] for c in PRODUCT_COLUMNS
                                                               if c in rec}}  # fmt: skip
            _save_products(list(current.values()), f"Import de « {up.name} »")


# --- Boutiques -------------------------------------------------------------------------------


def _part_editor(part: str, boutique: str, columns: list[str], label: str) -> None:
    ws = workspace()
    _, rows = ws.boutique_part(part, boutique)
    df = pd.DataFrame(
        [{c: r.get(c, "") for c in columns} for r in rows], columns=columns, dtype=str
    )
    edited = st.data_editor(df, num_rows="dynamic", hide_index=True, width="stretch",
                            key=f"part_{part}_{boutique}_{ss.nonce}",
                            column_config={c: st.column_config.TextColumn() for c in columns})  # fmt: skip
    if st.button(f"Enregistrer {label}", key=f"save_part_{part}"):
        if _need_author():
            return
        errors, warnings = ws.save_boutique_part(
            part, boutique, edited.to_dict("records"), author()
        )
        if errors:
            st.error("Rien n'est enregistré : " + " · ".join(errors))
            return
        reset_all()
        for w in warnings:
            st.warning(w)
        st.success(f"{label.capitalize()} de {boutique} enregistré(e)s.")


def _boutiques_list() -> None:
    ws = workspace()
    rows = ws.boutique_rows()
    df = pd.DataFrame(rows, columns=[*BOUTIQUE_COLUMNS, SLOTS, PORTFOLIO, B_ANOMALIES], dtype=str)
    with db_conn() as conn:
        planners = event_store.list_planners(conn)
    left, right = st.columns([3, 2])
    with left:
        st.caption(f"{len(df)} boutiques · le DC est une colonne · ouvre une boutique à droite "
                   "pour ses règles et ses créneaux de livraison.")  # fmt: skip
        edited = st.data_editor(
            df, num_rows="dynamic", hide_index=True, width="stretch", height=460,
            key=f"btq_grid_{ss.nonce}", disabled=[SLOTS, PORTFOLIO, B_ANOMALIES],
            column_config={
                CODE: st.column_config.TextColumn(width=70, required=True),
                "DC": st.column_config.SelectboxColumn(options=["MT1", "TO1", "CY1", "VA1"], width=80),
                PLANNER: st.column_config.SelectboxColumn(options=planners, width=130),
                SLOTS: st.column_config.TextColumn(width=70),
                PORTFOLIO: st.column_config.TextColumn(width=70),
            },
        )  # fmt: skip
        if st.button("Enregistrer les boutiques", type="primary", key="save_boutiques"):
            if _need_author():
                return
            errors = ws.save_boutiques(edited.to_dict("records"), author())
            if errors:
                st.error("Rien n'est enregistré : " + " · ".join(errors))
            else:
                reset_all()
                st.success("Boutiques enregistrées.")
    with right:
        codes = [r[CODE] for r in rows]
        b = st.selectbox("Ouvrir une boutique", codes, index=None, placeholder="Code boutique",
                         key="btq_open")  # fmt: skip
        if not b:
            st.info("Choisis une boutique pour voir et modifier ses règles et ses créneaux.")
            return
        row = next(r for r in rows if r[CODE] == b)
        st.markdown(f"**{b}** {row['Nom']} · DC {row['DC'] or '—'}"
                    + (f"  \n:orange[🟠 {row[B_ANOMALIES]}]" if row[B_ANOMALIES] else ""))  # fmt: skip
        st.markdown("**Règles boutique**")
        st.caption("max_pallets, max_units (sans SKU) · max_qty_sku, min_qty_sku (SKU obligatoire). "
                   "Une règle passe la ligne en REVIEW, elle ne change jamais une quantité.")  # fmt: skip
        _part_editor("rules", b, RULE_COLUMNS, "les règles")
        st.markdown("**Créneaux de livraison (Schedule)**")
        _part_editor("slots", b, SLOT_COLUMNS, "les créneaux")


def _portfolio_tab() -> None:
    codes = [r[CODE] for r in workspace().boutique_rows()]
    b = st.selectbox("Boutique", codes, index=codes.index("B80") if "B80" in codes else None,
                     placeholder="Code boutique", key="pf_btq")  # fmt: skip
    if not b:
        return
    st.caption("filter_out = Yes : SKU retiré de la commande · No : gardé. Comparaison en "
               "numérique puis en texte, comme Excel (7005.7 = 7005.70).")  # fmt: skip
    _part_editor("portfolio", b, ["sku", "filter_out"], "le portfolio")


def _boutiques_tab() -> None:
    _tab_header("boutiques")
    t1, t2 = st.tabs(["Boutiques", "Portfolio"])
    with t1:
        _boutiques_list()
    with t2:
        _portfolio_tab()


def _business_block() -> None:
    """Allocations, lancements et stocks cibles : en attendant leur passage aux Événements SKU."""
    with st.expander("Allocations, lancements et stocks cibles (bientôt dans Événements SKU)"):
        for key in ("allocations", "launches", "targets"):
            st.markdown(f"**{REF_BY_KEY[key].label}**")
            _table_editor(REF_BY_KEY[key])


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    return pd.read_excel(path, dtype=str).fillna("")


def _table_editor(ref: Referential) -> None:
    """Grille modifiable : cellules, lignes ajoutées ou supprimées, puis « Enregistrer »."""
    files = workspace().ref_files(ref)
    targets: list[Path | None] = list(files) or [None]
    for i, f in enumerate(targets):
        df = _read_table(f) if f else pd.DataFrame(columns=DEFAULT_COLUMNS[ref.key], dtype=str)
        st.caption((f"{f.name} · {len(df)} lignes" if f else "Aucun fichier : saisis les lignes ici.")
                   + " · clic sur une cellule pour la modifier, « + » en bas pour ajouter une ligne, "
                   "case à gauche puis Suppr pour retirer une ligne.")  # fmt: skip
        edited = st.data_editor(
            df, num_rows="dynamic", hide_index=True, width="stretch", height=320,
            key=f"tbl_{ref.key}_{i}_{ss.nonce}",
            column_config={c: st.column_config.TextColumn() for c in df.columns},
        )  # fmt: skip
        if st.button(f"Enregistrer {ref.label.lower()}" + (f" ({f.name})" if len(targets) > 1 and f else ""),
                     type="primary", key=f"save_tbl_{ref.key}_{i}"):  # fmt: skip
            if not author():
                st.error("Choisis ton prénom (Planner, barre de gauche).")
                return
            text = table_csv(list(edited.columns), edited.to_numpy(dtype=object).tolist())
            try:
                errors, warnings = workspace().save_table(
                    ref.key, text, author(), f.name if f else None
                )
            except ValueError as exc:
                errors, warnings = [str(exc)], []
            if errors:
                st.error("Rien n'est enregistré : " + " · ".join(errors))
                return
            reset_all()
            for w in warnings:
                st.warning(w)
            st.success(
                f"{ref.label} enregistré ({len(edited)} lignes). La commande sera recalculée."
            )


def calculator_import_block(key: str) -> None:
    """Importe multiples, conversions, DC, portfolio et Schedule depuis le calculateur."""
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    up = c1.file_uploader("Calculateur Excel (ex. « B80 105.xlsm »)", type=["xlsm", "xlsx"],
                          key=f"calc_{key}_{ss.nonce}")  # fmt: skip
    btq = c2.text_input("Boutique du calculateur", placeholder="ex. B80", key=f"calc_btq_{key}")
    if up is not None and st.button("Importer les référentiels du calculateur", type="primary",
                                    key=f"calc_btn_{key}"):  # fmt: skip
        if not author():
            st.error("Choisis ton prénom (Planner, barre de gauche).")
            return
        with st.spinner("Lecture du calculateur…"):
            reports = workspace().import_calculator(up.name, up.getvalue(),
                                                    btq.strip().upper(), author())  # fmt: skip
        rows = [r for rep in reports for r in rep.as_rows() if r["niveau"] != "INFO"]
        written = [r for rep in reports for r in rep.as_rows() if r["code"] == "WRITTEN"]
        if written:
            st.success(f"{len(written)} référentiel(s) importé(s).")
        if rows:
            st.dataframe(
                pd.DataFrame(rows).drop(columns=["code"]), hide_index=True, width="stretch"
            )
        reset_all()


def _use_test_referentials() -> None:
    workspace().use_test_referentials(author() or "Planner")
    reset_all()


def bootstrap_block(key: str) -> None:
    """Quand les référentiels manquent : importer le calculateur ou partir des données de test."""
    st.markdown("**Importer les référentiels depuis le calculateur Excel**")
    calculator_import_block(key)
    st.markdown("**Ou démarrer avec les référentiels de test**")
    st.caption("Multiples, conversions, DC et Schedule extraits du calculateur B80 le 05/10/2026, "
               "et le portfolio de B80. À remplacer dès que possible par ton calculateur à jour.")  # fmt: skip
    st.button("Utiliser les référentiels de test", key=f"test_refs_{key}",
              on_click=_use_test_referentials)  # fmt: skip


def _planners_block() -> None:
    """Liste des planners (prénom seulement) : identité, événements personnels, signatures."""
    with db_conn() as conn:
        names = event_store.list_planners(conn)
    st.caption("Prénoms seulement, jamais d'email. Retirer un planner ne supprime pas ses "
               "événements ni ses signatures.")  # fmt: skip
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    new = c1.text_input("Ajouter un planner", key="refs_new_planner", placeholder="Prénom")
    if c2.button("Ajouter", key="refs_add_planner", width="stretch") and new.strip():
        try:
            with db_conn() as conn:
                event_store.add_planner(conn, new)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.rerun()
    for name in names:
        a, b = st.columns([3, 1], vertical_alignment="center")
        a.write(name + (" (toi)" if name == author() else ""))
        if b.button("Retirer", key=f"refs_rm_{name}", width="stretch"):
            with db_conn() as conn:
                event_store.remove_planner(conn, name)
            st.rerun()


def render() -> None:
    st.header("Référentiels")
    with st.expander(
        "Importer depuis le calculateur Excel (.xlsm)", expanded=not workspace().has_masters()
    ):
        calculator_import_block("refs")
    tabs = st.tabs(["Produits", "Boutiques", "Planners"])
    with tabs[0]:
        _products_tab()
    with tabs[1]:
        _boutiques_tab()
    with tabs[2]:
        _tab_header("planners")
        _planners_block()
    _business_block()
