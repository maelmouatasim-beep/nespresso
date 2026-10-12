"""Génère la version web de démonstration de l'outil (données de test B80).

Le service Python complet (`run_planning` : sélection, prévision, arbitrage métier)
est exécuté ICI pour toutes les combinaisons proposées dans la page (mode, jours de
couverture, avec / sans exemples d'entrées métier). La page HTML ne calcule aucune
quantité : elle affiche le résultat qui correspond aux paramètres choisis, applique
les modifications du planner (arrondi au multiple, comme le moteur d'overrides) et
fait les totaux.

    python tools/build_demo_app.py      # écrit preview/outil_B80.html
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# L'espace de démo est recréé dans un dossier temporaire (rien n'est écrit dans data/).
os.environ.setdefault("COPILOT_DATA_DIR", tempfile.mkdtemp(prefix="copilot_demo_"))

from app.domain.events import (  # noqa: E402
    ALLOWED_EFFECTS,
    PERSONAL_EDITABLE_BY_OTHERS,
    PERSONAL_ONLY,
)
from app.domain.models import (  # noqa: E402
    OVERRIDE_CATEGORY_LABELS,
    Allocation,
    ForecastMode,
    Launch,
    PlanningParameters,
    TargetStock,
)
from app.engines.categories import CATEGORIES  # noqa: E402
from app.ingestion import referential_checks as checks  # noqa: E402
from app.ingestion.depot import freshness  # noqa: E402
from app.ingestion.events import DEMO_PLANNERS, example_events  # noqa: E402
from app.ingestion.fixtures_loader import read_csv_records  # noqa: E402
from app.ingestion.masters import all_boutiques, build_inputs_for_boutique  # noqa: E402
from app.ingestion.referential_edit import DEFAULT_COLUMNS  # noqa: E402
from app.services.planning import (  # noqa: E402
    RULE_VERSIONS,
    BusinessInputs,
    EventContext,
    run_planning,
)
from app.ui.workspace import (  # noqa: E402
    DEMO_EXTRACTION,
    DEPOT_LABELS,
    REFERENTIALS,
    demo_workspace,
)
from app.web.bridge import masters_payload  # noqa: E402
from app.web.serialize import Strings, line_meta, line_tuple  # noqa: E402

TEMPLATE = Path(__file__).with_name("demo_app_template.html")
ENGINE_DIR = ROOT / "preview" / "engine"
PYODIDE_PACKAGES = ("pydantic", "pydantic-core", "typing-extensions", "annotated-types",
                    "typing-inspection")  # fmt: skip
# Modules du moteur publiés avec la page (sans l'interface Streamlit ni les exports Excel).
ENGINE_EXCLUDED = ("app/ui/", "app/exports/")
OUTPUT = ROOT / "preview" / "outil_B80.html"
FIXTURES = ROOT / "tests" / "fixtures" / "b80_2026-10-05"
COVERS = [x / 2 for x in range(2, 43)]  # 1 à 21 jours
RUN_DATE = DEMO_EXTRACTION
# Démo : les événements SKU sont évalués pour la livraison prévue de B80 (jeu. 08/10).
DEMO_DELIVERY = date(2026, 10, 8)
_CREATED = datetime(2026, 10, 5, 8, 30)
DEMO_EVENTS = tuple(
    e.model_copy(
        update={"id": i, "created_by": "Démo", "created_at": _CREATED, "updated_at": _CREATED}
    )
    for i, e in enumerate(example_events(), 1)
)
# « team » : événements d'équipe seulement ; « all » : + la demande boutique personnelle
# (appliquée quand le planner est Camille, ou après « Reprendre »).
EVENT_VARIANTS = {"team": "", "all": "Camille"}
BOUTIQUE = "B80"
PREVIEW_ROWS = 150

EXAMPLES = BusinessInputs(
    allocations=(
        Allocation(
            boutique="B80",
            sku="7005.70",
            total_qty=4800,
            already_sent=0,
            wave_plan=(75, 25),
            current_wave=1,
            comment="Exemple : promo AOS 14-21 oct",
        ),
    ),  # fmt: skip
    launches=(
        Launch(
            boutique="B80",
            sku="7200.70",
            launch_date=date(2026, 10, 14),
            qty=3600,
            comment="Exemple : lancement Festive",
        ),
    ),  # fmt: skip
    targets=(TargetStock(boutique="B80", sku="142172", min_qty=20, target_qty=30, max_qty=60),),
)
EXAMPLE_ROWS = {
    "allocations": [{"Boutique": "B80", "SKU": "7005.70", "Total": "4800", "Déjà envoyé": "0",
                     "Vagues": "75/25", "Vague en cours": "1", "Commentaire": "Exemple : promo AOS 14-21 oct"}],
    "launches": [{"Boutique": "B80", "SKU": "7200.70", "Date de lancement": "14/10/2026",
                  "Quantité": "3600", "Commentaire": "Exemple : lancement Festive"}],
    "targets": [{"Boutique": "B80", "SKU": "142172", "Min": "20", "Cible": "30", "Max": "60"}],
}  # fmt: skip
FIXTURE_FILES = {
    "multiples": "master_multiples.csv",
    "conversions": "master_sku_conversions.csv",
    "portfolio": "master_boutique_portfolio_B80.csv",
    "dc_mapping": "master_dc_mapping.csv",
    "schedule": "master_schedule.csv",
}


def _preview_rows(key: str) -> tuple[int, list[dict]]:
    name = FIXTURE_FILES.get(key)
    if not name:
        return 0, []
    df = pd.read_csv(FIXTURES / name, dtype=str, keep_default_na=False)
    return len(df), df.head(PREVIEW_ROWS).to_dict("records")


# Lignes des exemples métier dans les colonnes des modèles (lues par les mêmes lecteurs).
EXAMPLE_TABLE_ROWS = {
    "allocations": [["B80", "7005.70", "4800", "0", "75/25", "1", "Exemple : promo AOS 14-21 oct"]],
    "launches": [["B80", "7200.70", "2026-10-14", "3600", "Exemple : lancement Festive"]],
    "targets": [["B80", "142172", "20", "30", "60"]],
    "rules": [],
}


def write_tables(exclusions) -> None:  # noqa: ANN001
    """Tables complètes des référentiels (modifiables dans la page) et exports de test.

    Chargées seulement quand le planner ouvre une table ou modifie quelque chose.
    """
    tables: dict[str, dict] = {}
    for key, name in FIXTURE_FILES.items():
        columns, records = read_csv_records(FIXTURES / name)
        tables[key] = {"columns": columns, "rows": [[r[c] for c in columns] for r in records]}
    for key, rows in EXAMPLE_TABLE_ROWS.items():
        tables[key] = {"columns": DEFAULT_COLUMNS[key], "rows": rows}
    tables["exclusions"] = {
        "columns": ["sku", "boutiques", "reason", "author", "updated_on"],
        "rows": [[e.sku, ";".join(e.boutiques) or "ALL", e.reason, e.author,
                  e.updated_on.isoformat()] for e in exclusions],
    }  # fmt: skip
    (ENGINE_DIR / "tables.json").write_text(
        json.dumps(tables, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    exports = {
        name: (FIXTURES / name).read_text(encoding="utf-8")
        for name in ("stock_situation_B80_CY1.csv", "stock_movements_B80.csv")
    }
    (ENGINE_DIR / "demo_exports.json").write_text(
        json.dumps(exports, ensure_ascii=False), encoding="utf-8"
    )


def write_engine(masters, exclusions) -> None:  # noqa: ANN001
    """Code Python du moteur + référentiels de test, pour le calcul dans le navigateur."""
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ENGINE_DIR / "app.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted((ROOT / "app").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if not rel.startswith(ENGINE_EXCLUDED):
                z.writestr(zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0)), path.read_bytes())
    # La plateforme ne sert pas les .zip / .whl : ils voyagent en base64 dans des .txt,
    # décodés par la page (aucun téléchargement extérieur).
    pyodide_dir = ROOT / "preview" / "pyodide"
    lock = json.loads((pyodide_dir / "pyodide-lock.json").read_text(encoding="utf-8"))
    blobs = ["python_stdlib.zip"] + [lock["packages"][p]["file_name"] for p in PYODIDE_PACKAGES]
    index = []
    for name in blobs:
        data = (pyodide_dir / name).read_bytes()
        (ENGINE_DIR / f"{name}.b64.txt").write_text(base64.b64encode(data).decode(), "ascii")
        index.append(name)
    data = (ENGINE_DIR / "app.zip").read_bytes()
    (ENGINE_DIR / "app.zip.b64.txt").write_text(base64.b64encode(data).decode(), "ascii")
    (ENGINE_DIR / "blobs.json").write_text(json.dumps(index), encoding="utf-8")
    payload = masters_payload(masters, exclusions, EXAMPLES)
    (ENGINE_DIR / "masters.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def build() -> Path:  # noqa: PLR0915 - un seul script linéaire, plus simple à relire
    ws = demo_workspace(reset=True)
    data = ws.load_day()
    masters = data.masters
    inputs, dc = build_inputs_for_boutique(
        BOUTIQUE, data.stock, data.movements, masters.products, masters.conversions,
        masters.dc_mapping, sources=data.source_hashes, history_days=7, fallback_weeks=4,
        portfolio=masters.portfolio.get(BOUTIQUE, {}), exclusions=data.exclusions,
    )  # fmt: skip
    write_engine(masters, data.exclusions)
    write_tables(data.exclusions)
    golden = pd.read_csv(FIXTURES / "golden_stock_cover_final_B80.csv", dtype=str)
    excel_qty = dict(zip(golden["sku"], golden["excel_suggested_qty"].astype(int), strict=True))
    excel_formula = dict(zip(golden["sku"], golden["excel_cell_is_formula"] == "True", strict=True))

    text = Strings()
    meta: list[dict] = []
    meta_idx: dict[str, int] = {}
    variants: dict[str, dict] = {}
    combos = [(v, b, ev) for v, b in (("none", BusinessInputs()), ("examples", EXAMPLES))
              for ev in EVENT_VARIANTS]  # fmt: skip
    for variant, business, ev in combos:
        context = EventContext(DEMO_EVENTS, EVENT_VARIANTS[ev])
        for mode in ForecastMode:
            key = f"{mode.value}|{variant}|{ev}"
            sel: list[int] = []
            per_line: list[list] = []
            warnings: list[str] = []
            for c_i, cover in enumerate(COVERS):
                params = PlanningParameters(
                    boutique=BOUTIQUE, run_date=RUN_DATE, delivery_date=DEMO_DELIVERY,
                    cover_days=cover, coffee_cover_days=None,
                )  # fmt: skip
                res = run_planning(inputs, params, mode, dc=dc, business_inputs=business,
                                   event_context=context)  # fmt: skip
                if c_i == 0:
                    warnings = list(res.rule_warnings)
                    for line in res.lines:
                        if line.sku not in meta_idx:
                            meta_idx[line.sku] = len(meta)
                            meta.append(
                                line_meta(line, masters.products, dc, excel_qty, excel_formula)
                            )
                        sel.append(meta_idx[line.sku])
                    per_line = [[] for _ in res.lines]
                assert [meta[i]["sku"] for i in sel] == [x.sku for x in res.lines]
                for i, line in enumerate(res.lines):
                    per_line[i].append(line_tuple(line, text))
            # Une seule valeur si la ligne ne dépend pas du cover (allège la page).
            packed = [cov[0] if all(t == cov[0] for t in cov) else cov for cov in per_line]
            variants[key] = {"sel": sel, "res": packed, "warnings": warnings}

    # Référentiels : propriétaire, mise à jour, anomalies, aperçu.
    meta_refs = ws.meta()
    anomalies = {
        "multiples": checks.multiples_anomalies(masters),
        "conversions": checks.conversions_anomalies(masters),
        "portfolio": checks.portfolio_anomalies(masters),
        "dc_mapping": checks.dc_mapping_anomalies(masters),
        "schedule": list(masters.schedule_issues),
        "exclusions": checks.exclusions_anomalies(data.exclusions, masters),
        "categories": checks.categories_anomalies(data.categories, masters),
    }
    refs = []
    for ref in REFERENTIALS:
        count, rows = _preview_rows(ref.key)
        if ref.key == "exclusions":
            rows = [{"SKU": x.sku, "Boutiques": ";".join(x.boutiques) or "ALL", "Raison": x.reason,
                     "Auteur": x.author, "Date": f"{x.updated_on:%d/%m/%Y}"} for x in data.exclusions]  # fmt: skip
            count = len(rows)
        m = meta_refs.get(ref.key, {})
        refs.append({
            "key": ref.key, "label": ref.label, "template": ref.template or "",
            "owner": m.get("owner", ""), "updated": m.get("updated_at", ""),
            "by": m.get("updated_by", ""), "src": m.get("source_file", ""),
            "anomalies": anomalies.get(ref.key, []), "count": count, "rows": rows,
            "examples": EXAMPLE_ROWS.get(ref.key, []),
        })  # fmt: skip

    # Dépôt du jour.
    depot = []
    for kind, (_, info) in sorted(ws.latest_depot().items()):
        day = date.fromisoformat(info["extraction"]) if info.get("extraction") else None
        fresh = freshness(day, RUN_DATE)
        depot.append({"kind": kind, "label": DEPOT_LABELS[kind], "name": info["original_name"],
                      "day": day.isoformat() if day else None, "how": info["extraction_how"],
                      "level": fresh.level, "fresh": fresh.label})  # fmt: skip
    dates = sorted({m.movement_date for m in data.movements})
    quality = [row for rep in data.reports for row in rep.as_rows()]

    names = masters.boutique_names
    payload = {
        "meta": {
            "boutique": BOUTIQUE,
            "dc": dc,
            "run_date": RUN_DATE.isoformat(),
            "history_days": inputs.sales.history_days,
            "rule_versions": RULE_VERSIONS,
            "covers": COVERS,
            "codes": sorted({m.movement_code for m in data.movements if m.location == BOUTIQUE}),
            "categories": [[c.value, label] for c, label in OVERRIDE_CATEGORY_LABELS.items()],
            "period": [dates[0].isoformat(), dates[-1].isoformat()] if dates else None,
        },  # fmt: skip
        "boutiques": [[b, names.get(b, "")] for b in all_boutiques(masters, data.stock)],
        "depot": depot,
        "dq": {
            "stock_rows": len(data.stock),
            "locations": len({x.location for x in data.stock}),
            "moves": len(data.movements),
            "days": len(dates),
        },  # fmt: skip
        "quality": quality,
        "refs": refs,
        "category_map": dict(data.categories),
        "category_names": list(CATEGORIES),
        "events": [e.model_dump(mode="json") for e in DEMO_EVENTS],
        "planners": list(DEMO_PLANNERS),
        "allowed_effects": {t.value: [e.value for e in es] for t, es in ALLOWED_EFFECTS.items()},
        "personal_only": sorted(t.value for t in PERSONAL_ONLY),
        "others_editable": PERSONAL_EDITABLE_BY_OTHERS,
        "demo_delivery": DEMO_DELIVERY.isoformat(),
        "text": text.items,
        "lines": meta,
        "variants": variants,
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    out = build()
    print(f"Outil web écrit : {out} ({out.stat().st_size / 1024:.0f} Ko)")
