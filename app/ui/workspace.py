"""Espace de travail local : dépôts du jour, référentiels et base d'audit.

Tout vit sous `data/` (jamais versionné). La démo utilise `data/demo/`, une copie des
données de test : la modifier ne touche jamais aux fichiers d'origine.
"""

from __future__ import annotations

import io
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from app.domain.models import Exclusion, StockMovement, StockSnapshotLine
from app.ingestion.business_inputs import (
    read_allocations,
    read_launches,
    read_rules,
    read_targets,
)
from app.ingestion.depot import Extraction, detect_extraction_date
from app.ingestion.exclusions import exclusions_csv, read_exclusions
from app.ingestion.masters import Masters, load_masters_dir
from app.ingestion.powerbi import read_movements, read_stock_situation
from app.ingestion.tables import ValidationReport
from app.services.planning import BusinessInputs
from app.storage.db import file_hash

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DEMO_SOURCE = ROOT / "tests" / "fixtures" / "b80_2026-10-05"
DEMO_EXTRACTION = date(2026, 10, 5)

STOCK, MOVES = "stock_situation", "stock_movements"
DEPOT_LABELS = {STOCK: "Stock Situation", MOVES: "Stock Movements"}


@dataclass(frozen=True)
class Referential:
    key: str
    label: str
    kind: str  # master | portfolio | exclusions | business
    filename: str  # nom du fichier (sans extension pour les modèles métier)
    template: str | None = None  # modèle vierge dans templates/


REFERENTIALS = (
    Referential("multiples", "Multiples", "master", "master_multiples.csv"),
    Referential("conversions", "Conversions", "master", "master_sku_conversions.csv"),
    Referential("portfolio", "Portfolio", "portfolio", "master_boutique_portfolio"),
    Referential("dc_mapping", "Boutiques et DC", "master", "master_dc_mapping.csv"),
    Referential("schedule", "Schedule", "master", "master_schedule.csv"),
    Referential("exclusions", "Exclusions", "exclusions", "exclusions.csv", "exclusions.xlsx"),
    Referential("allocations", "Allocations", "business", "allocations", "allocations.xlsx"),
    Referential("launches", "Lancements", "business", "launches", "launches.xlsx"),
    Referential("targets", "Stocks cibles", "business", "target_stock", "target_stock.xlsx"),
    Referential("rules", "Règles boutique", "business", "boutique_rules", "boutique_rules.xlsx"),
)
REF_BY_KEY = {r.key: r for r in REFERENTIALS}
BUSINESS_READERS = {
    "allocations": read_allocations,
    "launches": read_launches,
    "targets": read_targets,
    "rules": read_rules,
}


@dataclass
class DayData:
    """Tout ce qu'il faut pour calculer les commandes du jour."""

    stock: list[StockSnapshotLine]
    movements: list[StockMovement]
    masters: Masters
    business: BusinessInputs
    exclusions: list[Exclusion]
    reports: list[ValidationReport] = field(default_factory=list)
    source_hashes: dict[str, str] = field(default_factory=dict)
    extraction: dict[str, Extraction] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(r.has_errors for r in self.reports)


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.masters_dir = root / "masters"
        self.depot_dir = root / "depot"

    # --- base d'audit ---------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        env = os.environ.get("COPILOT_DB")
        return Path(env) if env else self.root / "copilot.db"

    # --- métadonnées des référentiels -------------------------------------------------

    def _meta_path(self) -> Path:
        return self.masters_dir / "_referentiels.json"

    def meta(self) -> dict[str, dict[str, str]]:
        try:
            return json.loads(self._meta_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def set_meta(self, key: str, **values: str) -> None:
        data = self.meta()
        data.setdefault(key, {}).update({k: v for k, v in values.items() if v is not None})
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    def _touch(self, key: str, author: str, source: str) -> None:
        self.set_meta(
            key,
            updated_at=datetime.now().isoformat(timespec="minutes"),
            updated_by=author,
            source_file=source,
        )

    # --- import des référentiels ---------------------------------------------------------

    def ref_files(self, ref: Referential) -> list[Path]:
        if not self.masters_dir.exists():
            return []
        if ref.kind == "portfolio":
            return sorted(self.masters_dir.glob("master_boutique_portfolio*.csv"))
        if ref.kind == "business":
            return sorted(self.masters_dir.glob(f"{ref.filename}.*"))
        path = self.masters_dir / ref.filename
        return [path] if path.exists() else []

    def import_referential(self, key: str, filename: str, data: bytes, author: str) -> Path:
        ref = REF_BY_KEY[key]
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower()
        if ref.kind == "business":
            if suffix not in (".csv", ".xlsx"):
                raise ValueError("Formats acceptés : .xlsx ou .csv")
            for old in self.ref_files(ref):
                old.unlink()
            target = self.masters_dir / f"{ref.filename}{suffix}"
        elif ref.kind == "portfolio":
            if suffix != ".csv":
                raise ValueError("Format accepté : .csv (produit par l'extraction du calculateur)")
            name = Path(filename).name
            if not name.startswith("master_boutique_portfolio"):
                name = f"master_boutique_portfolio_{Path(filename).stem}.csv"
            target = self.masters_dir / name
        elif ref.kind == "exclusions":
            exclusions, report = read_exclusions(io.BytesIO(data), filename)
            if report.has_errors:
                raise ValueError("; ".join(i.message for i in report.issues if i.level == "error"))
            self.save_exclusions(exclusions, author)
            return self.masters_dir / ref.filename
        else:
            if suffix != ".csv":
                raise ValueError("Format accepté : .csv (produit par l'extraction du calculateur)")
            target = self.masters_dir / ref.filename
        target.write_bytes(data)
        self._touch(key, author, Path(filename).name)
        return target

    def save_exclusions(self, exclusions: list[Exclusion], author: str) -> None:
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        path = self.masters_dir / REF_BY_KEY["exclusions"].filename
        path.write_text(exclusions_csv(exclusions), encoding="utf-8")
        self._touch("exclusions", author, path.name)

    def load_exclusions(self) -> tuple[list[Exclusion], ValidationReport]:
        path = self.masters_dir / REF_BY_KEY["exclusions"].filename
        if not path.exists():
            return [], ValidationReport(source="Exclusions")
        return read_exclusions(path, path.name)

    def load_business(self) -> tuple[BusinessInputs, list[ValidationReport]]:
        items: dict[str, tuple] = {}
        reports = []
        for key, reader in BUSINESS_READERS.items():
            files = self.ref_files(REF_BY_KEY[key])
            if not files:
                items[key] = ()
                continue
            parsed, rep = reader(files[0], files[0].name)
            items[key] = tuple(parsed)
            reports.append(rep)
        return BusinessInputs(
            allocations=items["allocations"], launches=items["launches"],
            targets=items["targets"], rules=items["rules"],
        ), reports  # fmt: skip

    def has_masters(self) -> bool:
        needed = ("multiples", "conversions", "dc_mapping")
        return all(self.ref_files(REF_BY_KEY[k]) for k in needed)

    # --- dépôt du jour ---------------------------------------------------------------------

    def save_depot(
        self, kind: str, filename: str, data: bytes, extraction: Extraction, today: date
    ) -> Path:
        folder = self.depot_dir / today.isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob(f"{kind}__*"):
            old.unlink()
        path = folder / f"{kind}__{Path(filename).name}"
        path.write_bytes(data)
        meta_path = folder / "_depot.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        meta[kind] = {
            "file": path.name,
            "original_name": Path(filename).name,
            "extraction": extraction.day.isoformat() if extraction.day else None,
            "extraction_how": extraction.how,
            "deposited_at": datetime.now().isoformat(timespec="minutes"),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        return path

    def set_extraction_date(self, kind: str, day: date, how: str = "confirmée par le planner"):
        latest = self.latest_depot()
        if kind not in latest:
            return
        folder = latest[kind][0].parent
        meta_path = folder / "_depot.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta[kind].update({"extraction": day.isoformat(), "extraction_how": how})
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")

    def latest_depot(self) -> dict[str, tuple[Path, dict]]:
        """Dernier fichier déposé pour chaque export, avec ses informations."""
        out: dict[str, tuple[Path, dict]] = {}
        if not self.depot_dir.exists():
            return out
        for folder in sorted(self.depot_dir.iterdir(), reverse=True):
            meta_path = folder / "_depot.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for kind, info in meta.items():
                path = folder / info["file"]
                if kind not in out and path.exists():
                    out[kind] = (path, info)
        return out

    # --- chargement complet --------------------------------------------------------------

    def load_day(self) -> DayData:
        depot = self.latest_depot()
        missing = [DEPOT_LABELS[k] for k in (STOCK, MOVES) if k not in depot]
        if missing:
            raise ValueError("Export manquant : " + ", ".join(missing))
        if not self.has_masters():
            raise ValueError("Référentiels manquants : importer au moins multiples, "
                             "conversions et boutiques/DC")  # fmt: skip
        stock_path, stock_info = depot[STOCK]
        moves_path, moves_info = depot[MOVES]
        stock, r1 = read_stock_situation(stock_path, stock_info["original_name"])
        moves, r2 = read_movements(moves_path, moves_info["original_name"])
        r1.source = f"Stock Situation ({stock_info['original_name']})"
        r2.source = f"Stock Movements ({moves_info['original_name']})"
        masters = load_masters_dir(self.masters_dir)
        business, business_reports = self.load_business()
        exclusions, excl_report = self.load_exclusions()
        hashes = {
            "stock_situation": file_hash(stock_path.read_bytes()),
            "stock_movements": file_hash(moves_path.read_bytes()),
        }
        for name in masters.sources.values():
            hashes[name] = file_hash((self.masters_dir / name).read_bytes())

        def extraction(info: dict) -> Extraction:
            day = date.fromisoformat(info["extraction"]) if info.get("extraction") else None
            return Extraction(day, info.get("extraction_how", ""))

        return DayData(
            stock=stock,
            movements=moves,
            masters=masters,
            business=business,
            exclusions=exclusions,
            reports=[r1, r2, *business_reports, excl_report],
            source_hashes=hashes,
            extraction={STOCK: extraction(stock_info), MOVES: extraction(moves_info)},
        )


def data_dir() -> Path:
    """Dossier des données locales (variable COPILOT_DATA_DIR pour les tests)."""
    env = os.environ.get("COPILOT_DATA_DIR")
    return Path(env) if env else DATA_DIR


def real_workspace() -> Workspace:
    return Workspace(data_dir())


def demo_workspace(reset: bool = False) -> Workspace:
    """Espace de démonstration (B80, 05-oct-2026), recréé depuis les données de test."""
    ws = Workspace(data_dir() / "demo")
    if reset and ws.root.exists():
        shutil.rmtree(ws.root)
    if ws.has_masters() and ws.latest_depot():
        return ws
    ws.masters_dir.mkdir(parents=True, exist_ok=True)
    for name in ("master_multiples.csv", "master_sku_conversions.csv", "master_dc_mapping.csv",
                 "master_schedule.csv", "master_boutique_portfolio_B80.csv"):  # fmt: skip
        shutil.copy(DEMO_SOURCE / name, ws.masters_dir / name)
    for ref in REFERENTIALS:
        if ref.kind != "exclusions":
            ws.set_meta(ref.key, owner="Équipe planning", updated_at="2026-10-05T08:20",
                        updated_by="Démo", source_file="données de démonstration")  # fmt: skip
    ws.save_exclusions(
        [
            Exclusion(
                sku="7010.70",
                reason="SKU e-commerce probable, non vendu en boutique (à vérifier)",
                author="Équipe planning",
                updated_on=date(2026, 10, 7),
            )
        ],
        "Démo",
    )
    ws.set_meta("exclusions", owner="Équipe planning")
    for kind, name in ((STOCK, "stock_situation_B80_CY1.csv"), (MOVES, "stock_movements_B80.csv")):
        data = (DEMO_SOURCE / name).read_bytes()
        ws.save_depot(kind, name, data, Extraction(DEMO_EXTRACTION, "données de démonstration"),
                      DEMO_EXTRACTION)  # fmt: skip
    return ws


def detect(filename: str, data: bytes) -> Extraction:
    return detect_extraction_date(filename, data)
