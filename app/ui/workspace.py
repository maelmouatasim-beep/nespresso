"""Espace de travail local : dépôts du jour, référentiels et base d'audit.

Tout vit sous `data/` (jamais versionné). La démo utilise `data/demo/`, une copie des
données de test : la modifier ne touche jamais aux fichiers d'origine.
"""

from __future__ import annotations

import io
import json
import os
import pickle
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from app.domain.models import Exclusion, StockMovement, StockSnapshotLine
from app.engines.categories import DEFAULT_CATEGORY_MAP
from app.ingestion.boutique_table import (
    EXTRA_FILE,
    BoutiqueSources,
    apply_boutique_edits,
    build_boutique_rows,
    replace_boutique_rows,
)
from app.ingestion.business_inputs import (
    read_allocations,
    read_launches,
    read_rules,
    read_targets,
)
from app.ingestion.calculator import extract
from app.ingestion.categories import categories_csv, read_categories
from app.ingestion.depot import Extraction, detect_extraction_date
from app.ingestion.exclusions import exclusions_csv, read_exclusions
from app.ingestion.fixtures_loader import read_csv_records
from app.ingestion.masters import Masters, load_masters_dir
from app.ingestion.powerbi import read_movements, read_stock_situation
from app.ingestion.product_table import ProductSources, apply_product_edits
from app.ingestion.referential_edit import DEFAULT_COLUMNS, check_table
from app.ingestion.tables import ValidationReport, read_raw
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
    kind: str  # master | portfolio | exclusions | categories | business
    filename: str  # nom du fichier (sans extension pour les modèles métier)
    template: str | None = None  # modèle vierge dans templates/


REFERENTIALS = (
    Referential("multiples", "Multiples", "master", "master_multiples.csv"),
    Referential("conversions", "Conversions", "master", "master_sku_conversions.csv"),
    Referential("portfolio", "Portfolio", "portfolio", "master_boutique_portfolio"),
    Referential("dc_mapping", "Boutiques et DC", "master", "master_dc_mapping.csv"),
    Referential("schedule", "Schedule", "master", "master_schedule.csv"),
    Referential("exclusions", "Exclusions", "exclusions", "exclusions.csv", "exclusions.xlsx"),
    Referential("categories", "Catégories", "categories", "categories.csv", "categories.xlsx"),
    # Anciens fichiers (D-042) : lus tant qu'ils ne sont pas convertis en événements SKU.
    Referential("allocations", "Allocations", "business", "allocations"),
    Referential("launches", "Lancements", "business", "launches"),
    Referential("targets", "Stocks cibles", "business", "target_stock"),
    Referential("rules", "Règles boutique", "business", "boutique_rules", "boutique_rules.xlsx"),
)
REF_BY_KEY = {r.key: r for r in REFERENTIALS}
DEMO_MASTER_FILES = {
    "multiples": "master_multiples.csv",
    "conversions": "master_sku_conversions.csv",
    "dc_mapping": "master_dc_mapping.csv",
    "schedule": "master_schedule.csv",
    "portfolio": "master_boutique_portfolio_B80.csv",
}
# Onglet du calculateur → référentiel (pour la date de mise à jour)
CALCULATOR_SHEET_KEYS = {
    "multiple list": "multiples", "multiples": "multiples",
    "sku conversion": "conversions", "conversion": "conversions",
    "dc mapping": "dc_mapping", "dc": "dc_mapping",
    "boutique portfolio": "portfolio", "portfolio": "portfolio",
    "schedule": "schedule",
}  # fmt: skip
LEGACY_BUSINESS_KEYS = ("allocations", "launches", "targets")
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
    categories: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_MAP))
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
        elif ref.kind == "categories":
            mapping, report = read_categories(io.BytesIO(data), filename)
            if report.has_errors:
                raise ValueError("; ".join(i.message for i in report.issues if i.level == "error"))
            self.save_categories(mapping, author)
            return self.masters_dir / ref.filename
        else:
            if suffix != ".csv":
                raise ValueError("Format accepté : .csv (produit par l'extraction du calculateur)")
            target = self.masters_dir / ref.filename
        target.write_bytes(data)
        self._touch(key, author, Path(filename).name)
        return target

    def save_table(
        self, key: str, csv_text: str, author: str, filename: str | None = None
    ) -> tuple[list[str], list[str]]:
        """Enregistre un référentiel modifié dans la grille de l'outil.

        La table est d'abord relue comme à l'import ; une erreur bloque l'écriture.
        `filename` : fichier existant à remplacer (portfolio : un fichier par boutique).
        Retourne (erreurs, avertissements).
        """
        ref = REF_BY_KEY[key]
        errors, warnings = check_table(key, csv_text)
        if errors:
            return errors, warnings
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        if ref.kind == "business":
            for old in self.ref_files(ref):
                old.unlink()
            target = self.masters_dir / f"{ref.filename}.csv"
        elif ref.kind == "portfolio":
            name = Path(filename).name if filename else "master_boutique_portfolio.csv"
            if not name.startswith("master_boutique_portfolio") or not name.endswith(".csv"):
                raise ValueError(f"Nom de fichier portfolio inattendu : {name}")
            target = self.masters_dir / name
        elif ref.kind == "master":
            target = self.masters_dir / ref.filename
        else:
            raise ValueError(f"{ref.label} : enregistré par son propre éditeur")
        target.write_text(csv_text, encoding="utf-8")
        self._touch(key, author, "modifié dans l'outil")
        return [], warnings

    # --- historique des modifications --------------------------------------------------

    def _history_path(self) -> Path:
        return self.masters_dir / "_historique.jsonl"

    def log_changes(self, key: str, author: str, changes: list[str]) -> None:
        if not changes:
            return
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        entry = {"at": datetime.now().isoformat(timespec="minutes"), "by": author,
                 "ref": key, "changes": changes}  # fmt: skip
        with self._history_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def history(self, key: str) -> list[dict]:
        """Modifications d'un onglet, les plus récentes d'abord."""
        try:
            lines = self._history_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = [json.loads(x) for x in lines if x.strip()]
        return [e for e in reversed(out) if e.get("ref") == key]

    # --- onglet Produits -------------------------------------------------------------------

    def _records(self, name: str) -> tuple[list[str], list[dict[str, str]]]:
        path = self.masters_dir / name
        return read_csv_records(path) if path.exists() else ([], [])

    def product_sources(self) -> ProductSources:
        categories, _ = self.load_categories()
        return ProductSources(
            multiples=self._records(REF_BY_KEY["multiples"].filename)[1],
            conversions=self._records(REF_BY_KEY["conversions"].filename)[1],
            exclusions=self._records(REF_BY_KEY["exclusions"].filename)[1],
            categories=categories,
        )

    def save_products(
        self, edited: list[dict], author: str, today: date | None = None
    ) -> tuple[list[str], list[str]]:
        """Enregistre le tableau Produits. Retourne (erreurs, avertissements)."""
        res = apply_product_edits(self.product_sources(), edited, author, today or date.today())
        if res.errors:
            return res.errors, []
        warnings: list[str] = []
        for key, text in (("multiples", res.multiples_csv), ("conversions", res.conversions_csv)):
            if key in res.touched:
                errors, warns = check_table(key, text)
                if errors:
                    return errors, []
                warnings += warns
        if "exclusions" in res.touched:
            _, report = read_exclusions(io.BytesIO(res.exclusions_csv.encode("utf-8")), "x.csv")
            if report.has_errors:
                return [i.message for i in report.issues if i.level == "error"], []
        files = {"multiples": res.multiples_csv, "conversions": res.conversions_csv,
                 "exclusions": res.exclusions_csv}  # fmt: skip
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        for key, text in files.items():
            if key in res.touched:
                (self.masters_dir / REF_BY_KEY[key].filename).write_text(text, encoding="utf-8")
                self._touch(key, author, "modifié dans l'outil")
        if "categories" in res.touched:
            self.save_categories(res.categories, author)
        if res.changes:
            self._touch("produits", author, "modifié dans l'outil")
            self.log_changes("produits", author, res.changes)
        return [], warnings

    # --- onglet Boutiques ------------------------------------------------------------------

    def boutique_sources(self) -> BoutiqueSources:
        sched_cols, sched = self._records(REF_BY_KEY["schedule"].filename)
        counts: dict[str, int] = {}
        for f in self.ref_files(REF_BY_KEY["portfolio"]):
            for r in read_csv_records(f)[1]:
                counts[r["boutique"].strip()] = counts.get(r["boutique"].strip(), 0) + 1
        return BoutiqueSources(
            dc_mapping=self._records(REF_BY_KEY["dc_mapping"].filename)[1],
            schedule=sched,
            schedule_columns=sched_cols or DEFAULT_COLUMNS["schedule"],
            extra=self._records(EXTRA_FILE)[1],
            portfolio_counts=counts,
        )

    def boutique_rows(self) -> list[dict[str, str]]:
        return build_boutique_rows(self.boutique_sources())

    def save_boutiques(self, edited: list[dict], author: str) -> list[str]:
        src = self.boutique_sources()
        res = apply_boutique_edits(src, edited)
        if res.errors:
            return res.errors
        for key, text in (("dc_mapping", res.dc_csv), ("schedule", res.schedule_csv)):
            errors, _ = check_table(key, text)
            if errors:
                return errors
        if not res.changes:
            return []
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        (self.masters_dir / REF_BY_KEY["dc_mapping"].filename).write_text(res.dc_csv, "utf-8")
        if src.schedule:
            (self.masters_dir / REF_BY_KEY["schedule"].filename).write_text(
                res.schedule_csv, "utf-8"
            )
        (self.masters_dir / EXTRA_FILE).write_text(res.extra_csv, "utf-8")
        self._touch("boutiques", author, "modifié dans l'outil")
        self.log_changes("boutiques", author, res.changes)
        return []

    def boutique_part(self, part: str, boutique: str) -> tuple[list[str], list[dict[str, str]]]:
        """Créneaux (Schedule), règles ou portfolio d'une boutique : (colonnes, lignes)."""
        columns, records = self._part_records(part, boutique)[:2]
        return columns, [r for r in records if r.get("boutique", "").strip() == boutique]

    def _part_records(self, part: str, boutique: str) -> tuple[list[str], list[dict], Path]:
        if part == "slots":
            path = self.masters_dir / REF_BY_KEY["schedule"].filename
            default = DEFAULT_COLUMNS["schedule"]
        elif part == "rules":
            files = self.ref_files(REF_BY_KEY["rules"])
            path = files[0] if files else self.masters_dir / "boutique_rules.csv"
            default = DEFAULT_COLUMNS["rules"]
        elif part == "portfolio":
            files = self.ref_files(REF_BY_KEY["portfolio"])
            path = next((f for f in files if any(r["boutique"].strip() == boutique
                                                 for r in read_csv_records(f)[1])),
                        self.masters_dir / f"master_boutique_portfolio_{boutique}.csv")  # fmt: skip
            default = DEFAULT_COLUMNS["portfolio"]
        else:
            raise ValueError(f"partie inconnue : {part}")
        if path.suffix == ".csv" and path.exists():
            columns, records = read_csv_records(path)
        elif path.exists():  # règles en .xlsx : relues en texte
            raw = read_raw(path, path.name)
            columns = raw.rows[0] if raw.rows else list(default)
            records = [dict(zip(columns, r, strict=False)) for r in raw.rows[1:]]
        else:
            columns, records = list(default), []
        return columns or list(default), records, path

    def save_boutique_part(
        self, part: str, boutique: str, rows: list[dict], author: str
    ) -> tuple[list[str], list[str]]:
        columns, records, path = self._part_records(part, boutique)
        old_name = next((r.get("boutique_name", "") for r in records
                         if r.get("boutique", "").strip() == boutique
                         and r.get("boutique_name", "").strip()), "")  # fmt: skip
        if part == "slots" and old_name and rows:  # le nom reste sur le premier créneau
            rows = [{**rows[0], "boutique_name": old_name}, *rows[1:]]
        text = replace_boutique_rows(records, columns, boutique, rows)
        key = {"slots": "schedule", "rules": "rules", "portfolio": "portfolio"}[part]
        errors, warnings = check_table(key, text)
        if errors:
            return errors, warnings
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        if path.suffix != ".csv":
            path.unlink()
            path = path.with_suffix(".csv")
        path.write_text(text, encoding="utf-8")
        label = {"slots": "créneaux", "rules": "règles", "portfolio": "portfolio"}[part]
        self._touch("boutiques", author, "modifié dans l'outil")
        self.log_changes(
            "boutiques", author, [f"{boutique} : {label} modifiés ({len(rows)} ligne(s))"]
        )
        return [], warnings

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

    def save_categories(self, mapping: dict[str, str], author: str) -> None:
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        path = self.masters_dir / REF_BY_KEY["categories"].filename
        path.write_text(categories_csv(mapping), encoding="utf-8")
        self._touch("categories", author, path.name)

    def load_categories(self) -> tuple[dict[str, str], ValidationReport]:
        """Table type produit → catégorie ; valeurs du planner si aucun fichier."""
        path = self.masters_dir / REF_BY_KEY["categories"].filename
        if not path.exists():
            return dict(DEFAULT_CATEGORY_MAP), ValidationReport(source="Catégories")
        mapping, report = read_categories(path, path.name)
        if report.has_errors:
            # Affichage seulement : on garde le classement par défaut, signalé, sans bloquer.
            fallback = ValidationReport(source=f"Catégories ({path.name})")
            details = "; ".join(i.message for i in report.issues if i.level == "error")
            fallback.add("warning", "CATEGORIES_INVALID",
                         f"Fichier invalide, classement par défaut utilisé : {details}")  # fmt: skip
            return dict(DEFAULT_CATEGORY_MAP), fallback
        return mapping, report

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

    # --- anciens fichiers allocations, lancements, stocks cibles (D-042) ------------------

    def legacy_business(self) -> tuple[BusinessInputs, list[Path], list[ValidationReport]]:
        """Allocations, lancements et stocks cibles des anciens fichiers, pas encore
        convertis en événements SKU. Ils comptent encore dans la commande d'ici là."""
        items: dict[str, tuple] = {}
        files: list[Path] = []
        reports = []
        for key in LEGACY_BUSINESS_KEYS:
            found = self.ref_files(REF_BY_KEY[key])
            files += found
            if not found:
                items[key] = ()
                continue
            parsed, rep = BUSINESS_READERS[key](found[0], found[0].name)
            items[key] = tuple(parsed)
            reports.append(rep)
        return BusinessInputs(
            allocations=items["allocations"], launches=items["launches"], targets=items["targets"]
        ), files, reports  # fmt: skip

    def archive_legacy_business(self, author: str, created: int) -> list[Path]:
        """Range les anciens fichiers dans masters/_archives/ (jamais supprimés)."""
        _, files, _ = self.legacy_business()
        folder = self.masters_dir / "_archives"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        moved = []
        for f in files:
            target = folder / f"{stamp}_{f.name}"
            f.rename(target)
            moved.append(target)
        self.log_changes("evenements", author, [
            f"{created} événement(s) créé(s) depuis les anciens fichiers "
            + ", ".join(f.name for f in files) + " (rangés dans masters/_archives)"
        ])  # fmt: skip
        return moved

    def import_calculator(
        self, filename: str, data: bytes, boutique: str | None, author: str
    ) -> list[ValidationReport]:
        """Extrait multiples, conversions, DC, portfolio et Schedule du calculateur Excel."""
        tmp_dir = self.root / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / Path(filename).name
        tmp.write_bytes(data)
        try:
            reports = extract(tmp, self.masters_dir, boutique or None)
        finally:
            tmp.unlink(missing_ok=True)
        for rep in reports:
            key = CALCULATOR_SHEET_KEYS.get(rep.source.split(" / ")[-1].strip().lower())
            if key and not rep.has_errors:
                self._touch(key, author, Path(filename).name)
        return reports

    def use_test_referentials(self, author: str) -> None:
        """Point de départ : référentiels des données de test (B80, 05-oct-2026)."""
        self.masters_dir.mkdir(parents=True, exist_ok=True)
        for key, name in DEMO_MASTER_FILES.items():
            shutil.copy(DEMO_SOURCE / name, self.masters_dir / name)
            self._touch(key, author, "référentiels de test (B80, 05-oct-2026)")

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
        for old in folder.glob(f"_cache_{kind}__*"):
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
        stock, r1 = _cached(stock_path, read_stock_situation, stock_info["original_name"])
        moves, r2 = _cached(moves_path, read_movements, moves_info["original_name"])
        r1.source = f"Stock Situation ({stock_info['original_name']})"
        r2.source = f"Stock Movements ({moves_info['original_name']})"
        masters = load_masters_dir(self.masters_dir)
        business, business_reports = self.load_business()
        exclusions, excl_report = self.load_exclusions()
        categories, cat_report = self.load_categories()
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
            categories=categories,
            reports=[r1, r2, *business_reports, excl_report, cat_report],
            source_hashes=hashes,
            extraction={STOCK: extraction(stock_info), MOVES: extraction(moves_info)},
        )


def _cached(path: Path, reader, original_name: str):  # noqa: ANN001, ANN202
    """Lecture d'un export, mise en cache à côté du fichier (données locales, jamais versionnées).

    Un export Power BI de ~90 000 lignes prend plusieurs secondes à lire : on ne le relit
    pas à chaque ouverture tant que le fichier n'a pas changé. Le cache garde des valeurs
    brutes déjà validées ; les objets sont reconstruits sans revalidation (rapide).
    """
    digest = file_hash(path.read_bytes())[:16]
    cache = path.parent / f"_cache_{path.stem}_{digest}.pkl"
    if cache.exists():
        try:
            with cache.open("rb") as fh:
                model, fields, rows, report = pickle.load(fh)  # noqa: S301 - écrit par l'outil
            return [
                model.model_construct(**dict(zip(fields, r, strict=True))) for r in rows
            ], report
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ValueError, TypeError):
            pass
    items, report = reader(path, original_name)
    for old in path.parent.glob(f"_cache_{path.stem}_*.pkl"):
        old.unlink(missing_ok=True)
    if items:
        model = type(items[0])
        fields = tuple(model.model_fields)
        rows = [tuple(getattr(x, f) for f in fields) for x in items]
        with cache.open("wb") as fh:
            pickle.dump((model, fields, rows, report), fh)
    return items, report


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
    for name in DEMO_MASTER_FILES.values():
        shutil.copy(DEMO_SOURCE / name, ws.masters_dir / name)
    keys = [r.key for r in REFERENTIALS if r.kind != "exclusions"]
    for key in [*keys, "produits", "boutiques", "planners"]:
        ws.set_meta(key, owner="Équipe planning", updated_at="2026-10-05T08:20",
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
