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
from app.engines.families import DISPLAY_ORDER, line_family  # noqa: E402
from app.engines.pallets import OTHERS, PER_PALLET, family  # noqa: E402
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

TEMPLATE = Path(__file__).with_name("demo_app_template.html")
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


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


class Strings:
    """Table de textes dédupliqués (raisons, étapes) pour alléger la page."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self._idx: dict[str, int] = {}

    def id(self, text: str | None) -> int:
        if text is None:
            return -1
        if text not in self._idx:
            self._idx[text] = len(self.items)
            self.items.append(text)
        return self._idx[text]


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:,.0f}".replace(",", " ")


def _fmt1(x: float | None) -> str:
    return "—" if x is None else f"{x:,.1f}".replace(",", " ").replace(".", ",")


def _business_step(e: dict) -> str | None:
    b = e.get("business")
    if not b:
        return None
    if "allocation" in b:
        a = b["allocation"]
        return (f"Allocation officielle : {_fmt(b['allocation_due'])} dus (total {_fmt(a['total_qty'])}, "
                f"vague {a['current_wave']}), arrondi au multiple : {_fmt(b['rounded_to_multiple'])}")  # fmt: skip
    if "launch" in b:
        la = b["launch"]
        d = date.fromisoformat(la["launch_date"])
        return f"Lancement le {d:%d/%m/%Y} : {_fmt(la['qty'])} − Expected, arrondi au multiple"
    if "target" in b:
        return "Stock cible appliqué (voir les raisons)"
    return None


def _fallback_step(e: dict) -> str | None:
    fb = e.get("fallback")
    if not fb:
        return None
    if fb["daily_rate"]:
        return (f"Historique de secours ({fb['weeks_used']} semaine(s) avec ventes sur "
                f"{fb['full_weeks_in_export']} dans l'export) : {_fmt1(fb['daily_rate'])} par jour → "
                f"{_fmt1(e['cover_applied'])} × {_fmt1(fb['daily_rate'])} arrondi au multiple : "
                f"{_fmt(fb['suggested_qty'])}")  # fmt: skip
    return (f"Historique de secours : aucune vente dans les semaines déposées "
            f"({fb['full_weeks_in_export']} semaine(s) complète(s) dans l'export)")  # fmt: skip


def _pallet_factor(line, masters, dc: str) -> float | None:  # noqa: ANN001
    fam = family(line)
    if fam == OTHERS:
        prod = masters.products.get(line.sku)
        upp = prod.units_per_pallet if prod else None
        return 1 / upp if upp else None
    return 1 / PER_PALLET[fam].get(dc, PER_PALLET[fam]["default"])


def _preview_rows(key: str) -> tuple[int, list[dict]]:
    name = FIXTURE_FILES.get(key)
    if not name:
        return 0, []
    df = pd.read_csv(FIXTURES / name, dtype=str, keep_default_na=False)
    return len(df), df.head(PREVIEW_ROWS).to_dict("records")


def build() -> Path:  # noqa: PLR0915 - un seul script linéaire, plus simple à relire
    ws = demo_workspace(reset=True)
    data = ws.load_day()
    masters = data.masters
    inputs, dc = build_inputs_for_boutique(
        BOUTIQUE, data.stock, data.movements, masters.products, masters.conversions,
        masters.dc_mapping, sources=data.source_hashes, history_days=7, fallback_weeks=4,
        portfolio=masters.portfolio.get(BOUTIQUE, {}), exclusions=data.exclusions,
    )  # fmt: skip
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
                            e, inp = line.explanation, line.explanation["inputs"]
                            fb = e.get("fallback") or {}
                            meta_idx[line.sku] = len(meta)
                            meta.append({
                                "sku": line.sku,
                                "desc": line.description or "",
                                "type": line.product_type or "",
                                "fam": line_family(line),
                                "pfam": family(line),
                                "pf": _pallet_factor(line, masters, dc),
                                "coffee": line.product_type == "C",
                                "sales": inp["sales_total"],
                                "sales_own": inp["sales_own"],
                                "sales_old": inp["sales_from_old_skus"],
                                "exp": inp["expected_total"],
                                "exp_own": inp["expected_own"],
                                "exp_old": inp["expected_from_old_skus"],
                                "dc": inp["dc_available"],
                                "cur": _r(e["current_cover_days"], 1),
                                "pfo": inp.get("in_portfolio"),
                                "excl": e.get("exclusion"),
                                "fbw": [fb.get("weeks_used"), fb.get("full_weeks_in_export")] if fb else None,
                                "xl": excel_qty.get(line.sku),
                                "xlf": excel_formula.get(line.sku),
                            })  # fmt: skip
                        sel.append(meta_idx[line.sku])
                    per_line = [[] for _ in res.lines]
                assert [meta[i]["sku"] for i in sel] == [x.sku for x in res.lines]
                for i, line in enumerate(res.lines):
                    e = line.explanation
                    rules = e["rules_triggered"]
                    conv = bool(e["inputs"]["sales_from_old_skus"]) or "old_sku_blocked" in rules \
                        or any("onversion" in r for r in line.reasons)  # fmt: skip
                    flags = (1 if conv else 0) | (
                        2 if ("dc_return" in rules or "dc_out_of_stock" in rules) else 0
                    )
                    per_line[i].append([
                        line.qty, _r(e["raw_need"]), line.status.value[0],
                        [text.id(r) for r in line.reasons], e["multiple"],
                        text.id(e["multiple_source"]), line.source.value, line.forecast_qty,
                        text.id(_fallback_step(e)), text.id(_business_step(e)), flags,
                    ])  # fmt: skip
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
            "families": list(DISPLAY_ORDER),
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
