"""Onglet « Boutiques » des référentiels : UNE ligne par boutique.

Code, nom, DC (DC Mapping), carrier (Schedule), planner habituel. Le nom, le planner
habituel et le carrier d'une boutique sans créneau au Schedule sont gardés dans
`master_boutiques.csv`. Les créneaux du Schedule, les règles boutique et le portfolio
d'une boutique se modifient à part (`replace_boutique_rows`). Sans pandas.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

CODE, NAME, DC, CARRIER, PLANNER = "Code", "Nom", "DC", "Carrier", "Planner habituel"
BOUTIQUE_COLUMNS = [CODE, NAME, DC, CARRIER, PLANNER]
SLOTS, PORTFOLIO, ANOMALIES = "Créneaux", "Portfolio", "Anomalies"
EXTRA_COLUMNS = ["boutique", "name", "carrier", "planner"]
EXTRA_FILE = "master_boutiques.csv"
KNOWN_DCS = ("MT1", "TO1", "CY1", "VA1")
Record = dict[str, str]


@dataclass
class BoutiqueSources:
    dc_mapping: list[Record]
    schedule: list[Record]
    schedule_columns: list[str]
    extra: list[Record]
    portfolio_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass
class BoutiqueSave:
    dc_csv: str = ""
    schedule_csv: str = ""
    extra_csv: str = ""
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def records_csv(columns: Sequence[str], records: Sequence[Mapping[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in records:
        w.writerow([r.get(c, "") for c in columns])
    return buf.getvalue()


def _clean(value: object) -> str:
    if value is None or (isinstance(value, float) and value != value):  # noqa: PLR0124 - NaN
        return ""
    return str(value).strip()


def _sort_key(code: str) -> tuple[int, str]:
    return (len(code), code)


def build_boutique_rows(src: BoutiqueSources) -> list[dict[str, str]]:
    dcs = {r["boutique"].strip(): r["dc"].strip() for r in src.dc_mapping}
    extra = {r["boutique"].strip(): r for r in src.extra}
    slots: dict[str, list[Record]] = {}
    for r in src.schedule:
        if r.get("boutique", "").strip():
            slots.setdefault(r["boutique"].strip(), []).append(r)
    codes = set(dcs) | set(extra) | set(slots) | set(src.portfolio_counts)
    rows = []
    for b in sorted(codes, key=_sort_key):
        e = extra.get(b, {})
        own = slots.get(b, [])
        sched_name = next((r["boutique_name"].strip() for r in own
                           if r.get("boutique_name", "").strip()), "")  # fmt: skip
        carriers = list(dict.fromkeys(r.get("carrier", "").strip() for r in own
                                      if r.get("carrier", "").strip()))  # fmt: skip
        issues = []
        dc = dcs.get(b, "")
        if not dc:
            issues.append("pas de DC : la commande ne peut pas être calculée")
        elif dc not in KNOWN_DCS:
            issues.append(f"DC inconnu « {dc} »")
        if not own:
            issues.append("aucun créneau au Schedule")
        rows.append({
            CODE: b,
            NAME: e.get("name", "").strip() or sched_name,
            DC: dc,
            CARRIER: " / ".join(carriers) if own else e.get("carrier", "").strip(),
            PLANNER: e.get("planner", "").strip(),
            SLOTS: str(len(own)),
            PORTFOLIO: str(src.portfolio_counts.get(b, 0)),
            ANOMALIES: "; ".join(issues),
        })  # fmt: skip
    return rows


def apply_boutique_edits(
    src: BoutiqueSources, edited: Sequence[Mapping[str, object]]
) -> BoutiqueSave:
    """Réécrit DC Mapping, Schedule (carrier, boutique retirée) et master_boutiques.csv."""
    out = BoutiqueSave()
    before = {r[CODE]: r for r in build_boutique_rows(src)}
    after: dict[str, dict[str, str]] = {}
    for i, raw in enumerate(edited, start=1):
        row = {c: _clean(raw.get(c)) for c in BOUTIQUE_COLUMNS}
        row[CODE], row[DC] = row[CODE].upper(), row[DC].upper()
        if not any(row.values()):
            continue
        if not row[CODE]:
            out.errors.append(f"ligne {i} : code boutique vide")
        elif row[CODE] in after:
            out.errors.append(f"boutique {row[CODE]} en double dans le tableau")
        else:
            after[row[CODE]] = row
    removed = [b for b in before if b not in after]
    dc_records = [{"boutique": b, "dc": r[DC]} for b, r in after.items() if r[DC]]
    order = [r["boutique"].strip() for r in src.dc_mapping]
    dc_records.sort(key=lambda r: (order.index(r["boutique"]) if r["boutique"] in order
                                   else len(order), _sort_key(r["boutique"])))  # fmt: skip
    has_slots = {r["boutique"].strip() for r in src.schedule if r.get("boutique", "").strip()}
    schedule = []
    for r in src.schedule:
        b = r.get("boutique", "").strip()
        if b in removed:
            continue
        if b in after and b in before and after[b][CARRIER] != before[b][CARRIER]:
            r = {**r, "carrier": after[b][CARRIER]}
        schedule.append(r)
    sched_names: dict[str, str] = {}
    for r in src.schedule:
        b, n = r.get("boutique", "").strip(), r.get("boutique_name", "").strip()
        if b and n:
            sched_names.setdefault(b, n)
    extra = []
    for b, r in after.items():
        old = before.get(b)
        # Le nom du Schedule n'est pas recopié : seul un nom différent est gardé à part.
        name = r[NAME] if r[NAME] != sched_names.get(b, "") else ""
        carrier = "" if b in has_slots else r[CARRIER]
        if name or carrier or r[PLANNER]:
            extra.append({"boutique": b, "name": name, "carrier": carrier, "planner": r[PLANNER]})
        if old is None:
            out.changes.append(f"{b} : ajoutée")
        else:
            diffs = [f"{c} {old[c] or '∅'} → {r[c] or '∅'}"
                     for c in BOUTIQUE_COLUMNS if r[c] != old[c]]  # fmt: skip
            if diffs:
                out.changes.append(f"{b} : " + ", ".join(diffs))
    out.changes += [f"{b} : retirée (DC, Schedule)" for b in removed]
    out.dc_csv = records_csv(["boutique", "dc"], dc_records)
    out.schedule_csv = records_csv(src.schedule_columns, schedule)
    out.extra_csv = records_csv(EXTRA_COLUMNS, extra)
    return out


def boutique_rows_of(records: Sequence[Record], boutique: str) -> list[Record]:
    return [r for r in records if r.get("boutique", "").strip() == boutique]


def replace_boutique_rows(
    records: Sequence[Record],
    columns: Sequence[str],
    boutique: str,
    new_rows: Sequence[Mapping[str, object]],
) -> str:
    """Remplace les lignes d'une boutique (créneaux, règles, portfolio) ; les autres
    boutiques restent identiques. La colonne boutique est remplie d'office."""
    kept = [r for r in records if r.get("boutique", "").strip() != boutique]
    added = []
    for raw in new_rows:
        row = {c: _clean(raw.get(c)) for c in columns if c != "boutique"}
        if any(row.values()):
            added.append({"boutique": boutique, **row})
    first = next((i for i, r in enumerate(records) if r.get("boutique", "").strip() == boutique),
                 len(kept))  # fmt: skip
    pos = sum(1 for r in records[:first] if r.get("boutique", "").strip() != boutique)
    return records_csv(columns, [*kept[:pos], *added, *kept[pos:]])
