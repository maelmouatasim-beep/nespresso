"""Écran 5 — Référentiels : propriétaire, mise à jour, import, anomalies."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.domain.models import Exclusion
from app.engines.categories import CATEGORIES
from app.ingestion import referential_checks as checks
from app.ingestion.categories import check_mapping
from app.ingestion.exclusions import parse_boutiques
from app.ingestion.masters import load_masters_dir
from app.ui.common import author, fmt_date, reset_all, ss, workspace
from app.ui.workspace import REFERENTIALS, ROOT, Referential

TEMPLATES = ROOT / "templates"


def _owner_changed(key: str) -> None:
    workspace().set_meta(key, owner=ss.get(f"owner_{key}", ""))


def _anomalies(ref: Referential) -> list[str]:
    ws = workspace()
    if not ws.has_masters():
        return []
    try:
        masters = load_masters_dir(ws.masters_dir)
    except ValueError as exc:
        return [str(exc)]
    if ref.key == "multiples":
        return checks.multiples_anomalies(masters)
    if ref.key == "conversions":
        return checks.conversions_anomalies(masters)
    if ref.key == "portfolio":
        return checks.portfolio_anomalies(masters)
    if ref.key == "dc_mapping":
        return checks.dc_mapping_anomalies(masters)
    if ref.key == "schedule":
        return masters.schedule_issues
    if ref.key == "categories":
        mapping, rep = ws.load_categories()
        return [i.message for i in rep.issues if i.level != "info"] + checks.categories_anomalies(
            mapping, masters
        )
    if ref.key == "exclusions":
        exclusions, rep = ws.load_exclusions()
        return [i.message for i in rep.issues if i.level != "info"] + checks.exclusions_anomalies(
            exclusions, masters
        )
    _, reports = ws.load_business()
    return [f"{r.source} : {i.message}" for r in reports for i in r.issues
            if i.level != "info" and r.source.startswith(ref.filename)]  # fmt: skip


def _preview(ref: Referential) -> None:
    files = workspace().ref_files(ref)
    if not files:
        st.info("Aucun fichier.")
        return
    for f in files:
        if f.suffix == ".csv":
            df = pd.read_csv(f, dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(f, dtype=str).fillna("")
        st.caption(f"{f.name} · {len(df)} lignes")
        st.dataframe(df.head(300), hide_index=True, width="stretch", height=260)


def _exclusions_editor() -> None:
    ws = workspace()
    exclusions, _ = ws.load_exclusions()
    df = pd.DataFrame(
        [
            {
                "SKU": e.sku,
                "Boutiques": ";".join(e.boutiques) or "ALL",
                "Raison": e.reason,
                "Auteur": e.author,
                "Date": e.updated_on,
            }
            for e in exclusions
        ],  # fmt: skip
        columns=["SKU", "Boutiques", "Raison", "Auteur", "Date"],
    )
    edited = st.data_editor(
        df, num_rows="dynamic", hide_index=True, width="stretch", key=f"excl_{ss.nonce}",
        column_config={
            "SKU": st.column_config.TextColumn(width=110, required=True),
            "Boutiques": st.column_config.TextColumn(width=140, help="ALL ou codes séparés par ;"),
            "Raison": st.column_config.TextColumn(width=360, required=True),
            "Auteur": st.column_config.TextColumn(width=140),
            "Date": st.column_config.DateColumn(format="DD/MM/YYYY", width=110),
        },
    )  # fmt: skip
    if st.button("Enregistrer les exclusions", type="primary", key="save_excl"):
        if not author():
            st.error("Indique ton nom dans la barre de gauche.")
            return
        rows, errors = [], []
        for i, r in enumerate(edited.to_dict("records"), start=1):
            if not r["SKU"] or pd.isna(r["SKU"]):
                continue
            try:
                rows.append(Exclusion(
                    sku=str(r["SKU"]).strip(), boutiques=parse_boutiques(str(r["Boutiques"] or "")),
                    reason=str(r["Raison"] or ""), author=str(r["Auteur"] or "") or author(),
                    updated_on=pd.Timestamp(r["Date"]).date() if r["Date"] and not pd.isna(r["Date"]) else date.today(),
                ))  # fmt: skip
            except ValidationError as exc:
                errors.append(f"ligne {i} : {exc.errors()[0]['msg']}")
        if errors:
            st.error(" · ".join(errors))
            return
        ws.save_exclusions(rows, author())
        reset_all()
        st.success(f"{len(rows)} exclusion(s) enregistrée(s).")


def _categories_editor() -> None:
    ws = workspace()
    mapping, _ = ws.load_categories()
    order = {c: i for i, c in enumerate(CATEGORIES)}
    df = pd.DataFrame(
        sorted(mapping.items(), key=lambda kv: (order[kv[1]], kv[0])),
        columns=["Type produit", "Catégorie"],
    )
    st.caption("Classement des types produit dans le filtre « Catégorie » de la commande. "
               "Un type absent est classé « Autres ». Aucune quantité n'en dépend.")  # fmt: skip
    edited = st.data_editor(
        df, num_rows="dynamic", hide_index=True, key=f"cats_{ss.nonce}", width=420,
        column_config={
            "Type produit": st.column_config.TextColumn(width=120, required=True),
            "Catégorie": st.column_config.SelectboxColumn(options=list(CATEGORIES), width=220,
                                                          required=True),
        },
    )  # fmt: skip
    if st.button("Enregistrer les catégories", type="primary", key="save_cats"):
        if not author():
            st.error("Indique ton nom dans la barre de gauche.")
            return
        rows = [(str(r["Type produit"] or ""), str(r["Catégorie"] or ""))
                for r in edited.to_dict("records")
                if not (pd.isna(r["Type produit"]) and pd.isna(r["Catégorie"]))]  # fmt: skip
        new, errors = check_mapping(rows)
        if errors:
            st.error(" · ".join(errors))
            return
        ws.save_categories(new, author())
        ss.day, ss.day_error = None, None  # recharge l'affichage ; la commande reste valable
        st.success(f"{len(new)} type(s) produit enregistré(s).")


def _referential_tab(ref: Referential) -> None:
    ws = workspace()
    meta = ws.meta().get(ref.key, {})
    c1, c2 = st.columns([1.2, 3])
    c1.text_input("Propriétaire", value=meta.get("owner", ""), key=f"owner_{ref.key}",
                  on_change=_owner_changed, args=(ref.key,))  # fmt: skip
    when = meta.get("updated_at")
    c2.markdown(
        f"Mis à jour le **{fmt_date(date.fromisoformat(when[:10])) if when else '—'}**"
        + (f" à {when[11:16]}" if when and len(when) > 11 else "")
        + (f" par {meta['updated_by']}" if meta.get("updated_by") else "")
        + (f" · source : `{meta['source_file']}`" if meta.get("source_file") else "")
    )
    issues = _anomalies(ref)
    if issues:
        with st.expander(f"⚠️ {len(issues)} anomalie(s)", expanded=True):
            for i in issues:
                st.markdown(f"- {i}")
    else:
        st.success("Aucune anomalie.")
    if ref.kind == "exclusions":
        _exclusions_editor()
    elif ref.kind == "categories":
        _categories_editor()
    else:
        _preview(ref)
    u1, u2 = st.columns([3, 1], vertical_alignment="bottom")
    up = u1.file_uploader("Importer (remplace le fichier actuel)", type=["csv", "xlsx"],
                          key=f"imp_{ref.key}_{ss.nonce}")  # fmt: skip
    if up is not None and u1.button("Importer", key=f"imp_btn_{ref.key}"):
        if not author():
            st.error("Indique ton nom dans la barre de gauche.")
        else:
            try:
                ws.import_referential(ref.key, up.name, up.getvalue(), author())
                reset_all()
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if ref.template and (TEMPLATES / ref.template).exists():
        u2.download_button("Modèle vierge", data=(TEMPLATES / ref.template).read_bytes(),
                           file_name=ref.template, key=f"tpl_{ref.key}")  # fmt: skip


def calculator_import_block(key: str) -> None:
    """Importe multiples, conversions, DC, portfolio et Schedule depuis le calculateur."""
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    up = c1.file_uploader("Calculateur Excel (ex. « B80 105.xlsm »)", type=["xlsm", "xlsx"],
                          key=f"calc_{key}_{ss.nonce}")  # fmt: skip
    btq = c2.text_input("Boutique du calculateur", placeholder="ex. B80", key=f"calc_btq_{key}")
    if up is not None and st.button("Importer les référentiels du calculateur", type="primary",
                                    key=f"calc_btn_{key}"):  # fmt: skip
        if not author():
            st.error("Indique ton nom dans la barre de gauche.")
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


def render() -> None:
    st.header("Référentiels")
    with st.expander(
        "Importer depuis le calculateur Excel (.xlsm)", expanded=not workspace().has_masters()
    ):
        calculator_import_block("refs")
    tabs = st.tabs([r.label for r in REFERENTIALS])
    for tab, ref in zip(tabs, REFERENTIALS, strict=True):
        with tab:
            _referential_tab(ref)
