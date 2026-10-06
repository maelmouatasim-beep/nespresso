"""Chargement des données pour l'interface (sans Streamlit, donc testable).

Deux sources :
- la fixture de démonstration B80 (données de test approuvées) ;
- les fichiers du planner (exports Power BI + tables maîtres + modèles métier).
Les fichiers maîtres envoyés sont copiés dans `data/masters/` (local, non versionné)
pour être réutilisés aux runs suivants.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.domain.models import StockMovement, StockSnapshotLine
from app.ingestion.business_inputs import read_allocations, read_launches, read_targets
from app.ingestion.masters import Masters, load_masters_dir
from app.ingestion.powerbi import read_movements, read_stock_situation
from app.ingestion.tables import ValidationReport
from app.services.planning import BusinessInputs
from app.storage.db import file_hash

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "tests" / "fixtures" / "b80_2026-10-05"
MASTERS_DIR = ROOT / "data" / "masters"
MASTER_FILE_PREFIX = "master_"


class Upload(Protocol):
    """Ce qu'il faut d'un fichier envoyé (compatible avec st.UploadedFile)."""

    name: str

    def getvalue(self) -> bytes: ...


@dataclass
class LoadedData:
    label: str
    stock: list[StockSnapshotLine]
    movements: list[StockMovement]
    masters: Masters
    business: BusinessInputs = field(default_factory=BusinessInputs)
    reports: list[ValidationReport] = field(default_factory=list)
    source_hashes: dict[str, str] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(r.has_errors for r in self.reports)


def load_demo() -> LoadedData:
    ss_path = DEMO_DIR / "stock_situation_B80_CY1.csv"
    mv_path = DEMO_DIR / "stock_movements_B80.csv"
    stock, r1 = read_stock_situation(ss_path, ss_path.name)
    moves, r2 = read_movements(mv_path, mv_path.name)
    masters = load_masters_dir(DEMO_DIR)
    return LoadedData(
        label="Démo B80 (05-oct-2026)",
        stock=stock,
        movements=moves,
        masters=masters,
        reports=[r1, r2, _masters_report(masters)],
        source_hashes={p.name: file_hash(p.read_bytes()) for p in (ss_path, mv_path)},
    )


def save_master_uploads(files: Sequence[Upload], directory: Path = MASTERS_DIR) -> list[str]:
    """Copie les fichiers master_*.csv envoyés. Refuse tout autre nom."""
    directory.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.name).name
        if not (name.startswith(MASTER_FILE_PREFIX) and name.endswith(".csv")):
            raise ValueError(f"{name} : seuls les fichiers master_*.csv sont acceptés")
        (directory / name).write_bytes(f.getvalue())
        saved.append(name)
    return saved


def load_uploads(
    stock_file: Upload,
    movements_file: Upload,
    *,
    masters_dir: Path = MASTERS_DIR,
    allocations_file: Upload | None = None,
    launches_file: Upload | None = None,
    targets_file: Upload | None = None,
) -> LoadedData:
    stock, r1 = read_stock_situation(io.BytesIO(stock_file.getvalue()), stock_file.name)
    moves, r2 = read_movements(io.BytesIO(movements_file.getvalue()), movements_file.name)
    masters = load_masters_dir(masters_dir)
    reports = [r1, r2, _masters_report(masters)]
    hashes = {f.name: file_hash(f.getvalue()) for f in (stock_file, movements_file)}
    allocs, launches, targets = (), (), ()
    if allocations_file is not None:
        items, rep = read_allocations(
            io.BytesIO(allocations_file.getvalue()), allocations_file.name
        )
        allocs, hashes[allocations_file.name] = tuple(items), file_hash(allocations_file.getvalue())
        reports.append(rep)
    if launches_file is not None:
        items, rep = read_launches(io.BytesIO(launches_file.getvalue()), launches_file.name)
        launches, hashes[launches_file.name] = tuple(items), file_hash(launches_file.getvalue())
        reports.append(rep)
    if targets_file is not None:
        items, rep = read_targets(io.BytesIO(targets_file.getvalue()), targets_file.name)
        targets, hashes[targets_file.name] = tuple(items), file_hash(targets_file.getvalue())
        reports.append(rep)
    for name in masters.sources.values():
        path = masters_dir / name
        hashes[name] = file_hash(path.read_bytes())
    return LoadedData(
        label="Fichiers chargés",
        stock=stock,
        movements=moves,
        masters=masters,
        business=BusinessInputs(allocations=allocs, launches=launches, targets=targets),
        reports=reports,
        source_hashes=hashes,
    )


def _masters_report(masters: Masters) -> ValidationReport:
    rep = ValidationReport(source="Tables maîtres")
    if masters.product_list_anomalies:
        rep.add(
            "warning",
            "MULTIPLE_LIST",
            f"{len(masters.product_list_anomalies)} SKU en double ou mal formés dans la "
            f"Multiple list (première ligne utilisée), ex. {masters.product_list_anomalies[:5]}",
        )
    zero = [p.sku for p in masters.products.values() if p.order_multiple == 0]
    if zero:
        rep.add("warning", "ZERO_MULTIPLE", f"{len(zero)} SKU avec multiple 0, ex. {zero[:5]}")
    return rep
