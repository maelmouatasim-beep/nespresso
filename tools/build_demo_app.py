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

import json
import os
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# L'espace de démo est recréé dans un dossier temporaire (rien n'est écrit dans data/).
os.environ.setdefault("COPILOT_DATA_DIR", tempfile.mkdtemp(prefix="copilot_demo_"))

from app.domain.models import (  # noqa: E402
    OVERRIDE_CATEGORY_LABELS,
    Allocation,
    ForecastMode,
    Launch,
    PlanningParameters,
    TargetStock,
)
from app.engines.categories import CATEGORIES  # noqa: E402
from app.engines.schedule import boutiques_ordering_on  # noqa: E402
from app.ingestion import referential_checks as checks  # noqa: E402
from app.ingestion.depot import freshness  # noqa: E402
from app.ingestion.masters import all_boutiques, build_inputs_for_boutique  # noqa: E402
from app.services.planning import RULE_VERSIONS, BusinessInputs, run_planning  # noqa: E402
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
# Modules du moteur publiés avec la page (sans l'interface Streamlit ni les exports Excel).
ENGINE_EXCLUDED = ("app/ui/", "app/exports/")
OUTPUT = ROOT / "preview" / "outil_B80.html"
FIXTURES = ROOT / "tests" / "fixtures" / "b80_2026-10-05"
COVERS = [x / 2 for x in range(2, 43)]  # 1 à 21 jours
RUN_DATE = DEMO_EXTRACTION
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


def write_engine(masters, exclusions) -> None:  # noqa: ANN001
    """Code Python du moteur + référentiels de test, pour le calcul dans le navigateur."""
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ENGINE_DIR / "app.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted((ROOT / "app").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if not rel.startswith(ENGINE_EXCLUDED):
                z.writestr(zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0)), path.read_bytes())
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
    golden = pd.read_csv(FIXTURES / "golden_stock_cover_final_B80.csv", dtype=str)
    excel_qty = dict(zip(golden["sku"], golden["excel_suggested_qty"].astype(int), strict=True))
    excel_formula = dict(zip(golden["sku"], golden["excel_cell_is_formula"] == "True", strict=True))

    text = Strings()
    meta: list[dict] = []
    meta_idx: dict[str, int] = {}
    variants: dict[str, dict] = {}
    for variant, business in (("none", BusinessInputs()), ("examples", EXAMPLES)):
        for mode in ForecastMode:
            key = f"{mode.value}|{variant}"
            sel: list[int] = []
            per_line: list[list] = []
            warnings: list[str] = []
            for c_i, cover in enumerate(COVERS):
                params = PlanningParameters(
                    boutique=BOUTIQUE, run_date=RUN_DATE, delivery_date=RUN_DATE,
                    cover_days=cover, coffee_cover_days=None,
                )  # fmt: skip
                res = run_planning(inputs, params, mode, dc=dc, business_inputs=business)
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
    planned = boutiques_ordering_on(masters.schedule, RUN_DATE)
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
        "planned": [[d.boutique, d.delivery_date.isoformat()] for d in planned],
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
