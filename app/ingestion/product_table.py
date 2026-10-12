"""Onglet « Produits » des référentiels : UNE ligne par SKU.

Réunit la Multiple list (description, type, multiple, unités par palette), la catégorie
(table type produit → catégorie), les conversions (ancien SKU remplacé et date) et les
exclusions. Le planner modifie le tableau ; `apply_product_edits` réécrit les fichiers
sources, en ne touchant qu'aux SKU réellement modifiés (les autres lignes restent
identiques, doublons compris). Sans pandas : le même code tourne dans la version web.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from app.domain.models import COFFEE_TYPE
from app.engines.categories import CATEGORIES, category_of, normalize_type
from app.engines.forecast import infer_coffee_multiple
from app.ingestion.fixtures_loader import CONVERSION_COLUMNS, MULTIPLE_COLUMNS

SKU, DESC, TYPE, CAT, MULT, PALLET = (
    "SKU", "Description", "Type", "Catégorie", "Multiple", "Unités par palette",
)  # fmt: skip
OLD, CONV_DATE, EXCL, EXCL_REASON = (
    "Ancien SKU remplacé", "Date conversion", "Exclu", "Raison exclusion",
)  # fmt: skip
PRODUCT_COLUMNS = [SKU, DESC, TYPE, CAT, MULT, PALLET, OLD, CONV_DATE, EXCL, EXCL_REASON]
ANOMALIES = "Anomalies"
EXCLUSION_COLUMNS = ["sku", "boutiques", "reason", "author", "updated_on"]
NO_REPLACEMENT = "XYZ123"  # conversion sans remplaçant : « ne plus envoyer »
YES = {"oui", "yes", "o", "x", "all", "toutes"}
Record = dict[str, str]


@dataclass
class ProductSources:
    """Contenu brut des fichiers sources (texte), tel qu'il est sur disque."""

    multiples: list[Record]
    conversions: list[Record]
    exclusions: list[Record]
    categories: Mapping[str, str]


@dataclass
class ProductSave:
    multiples_csv: str = ""
    conversions_csv: str = ""
    exclusions_csv: str = ""
    categories: dict[str, str] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)  # pour l'historique
    errors: list[str] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)  # multiples / conversions / ...


def _csv(columns: Sequence[str], records: Sequence[Mapping[str, str]]) -> str:
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


def _excl_cell(boutiques: str) -> str:
    b = boutiques.strip()
    return "oui" if b.upper() in ("", "ALL") else b


def build_product_rows(src: ProductSources) -> list[dict[str, str]]:
    """Une ligne par SKU, avec ses anomalies (texte, séparées par « ; »)."""
    by_sku: dict[str, list[Record]] = {}
    for r in src.multiples:
        by_sku.setdefault(r["sku"].strip(), []).append(r)
    olds: dict[str, list[Record]] = {}
    for c in src.conversions:
        olds.setdefault(c["new_sku"].strip(), []).append(c)
    excl = {e["sku"].strip(): e for e in src.exclusions}
    order = list(by_sku) + [s for s in [*olds, *excl] if s not in by_sku]
    rows: list[dict[str, str]] = []
    for sku in dict.fromkeys(order):
        recs = by_sku.get(sku, [])
        clean = [r for r in recs if r["sku"] == r["sku"].strip()]
        first = (clean or recs or [{}])[0]
        ptype = first.get("type", "").strip()
        mult = first.get("order_multiple", "").strip()
        desc = first.get("description", "").strip()
        convs = olds.get(sku, [])
        e = excl.get(sku)
        row = {
            SKU: sku,
            DESC: desc or ("Pas de remplaçant : ne plus envoyer" if sku == NO_REPLACEMENT else ""),
            TYPE: ptype,
            CAT: category_of(ptype, src.categories) if ptype else "",
            MULT: mult,
            PALLET: first.get("units_per_pallet", "").strip(),
            OLD: ";".join(c["old_sku"].strip() for c in convs),
            CONV_DATE: ";".join(dict.fromkeys(c["effective_date"].strip() for c in convs)),
            EXCL: _excl_cell(e["boutiques"]) if e else "",
            EXCL_REASON: e["reason"].strip() if e else "",
        }
        issues: list[str] = []
        if len(recs) > 1:
            values = sorted({r["order_multiple"].strip() for r in recs})
            detail = f", multiples {' / '.join(values)}" if len(values) > 1 else ""
            issues.append(f"doublon ({len(recs)} lignes{detail})")
        if any(r["sku"] != r["sku"].strip() for r in recs):
            issues.append("espace parasite autour du SKU")
        coffee = normalize_type(ptype) == COFFEE_TYPE
        if coffee and mult in ("0", "1"):
            issues.append(f"multiple {mult} sur un café")
        if recs and not mult:
            inferred = infer_coffee_multiple(desc) if coffee else None
            issues.append(f"multiple inféré ({inferred})" if inferred else "multiple manquant")
        if not recs and sku != NO_REPLACEMENT:
            issues.append("absent de la Multiple list")
        row[ANOMALIES] = "; ".join(issues)
        rows.append(row)
    return rows


def _split(text: str) -> list[str]:
    return [x.strip() for x in text.replace(",", ";").split(";") if x.strip()]


def apply_product_edits(
    src: ProductSources,
    edited: Sequence[Mapping[str, object]],
    author: str,
    today: date,
) -> ProductSave:
    """Réécrit les fichiers sources à partir du tableau modifié.

    Seuls les SKU dont une cellule a changé sont réécrits. Un multiple, un type, une
    description ou des unités par palette modifiés s'appliquent à TOUTES les lignes du
    SKU dans la Multiple list (doublons compris). Une ligne retirée supprime le SKU de
    la Multiple list, ses conversions (comme nouveau SKU) et son exclusion.
    """
    out = ProductSave(categories=dict(src.categories))
    before = {r[SKU]: r for r in build_product_rows(src)}
    after: dict[str, dict[str, str]] = {}
    for i, raw in enumerate(edited, start=1):
        row = {c: _clean(raw.get(c)) for c in PRODUCT_COLUMNS}
        if not any(row.values()):
            continue
        if not row[SKU]:
            out.errors.append(f"ligne {i} : SKU vide")
            continue
        if row[SKU] in after:
            out.errors.append(f"SKU {row[SKU]} en double dans le tableau")
            continue
        after[row[SKU]] = row
    removed = [s for s in before if s not in after]
    changed = {s for s, r in after.items() if s not in before or any(
        r[c] != before[s][c] for c in PRODUCT_COLUMNS)}  # fmt: skip

    # Catégorie : elle dépend du type produit ; la changer reclasse tout ce type.
    cat_changes: dict[str, str] = {}
    for s in sorted(changed):
        r, old = after[s], before.get(s)
        if old is not None and r[CAT] == old[CAT] and r[TYPE] == old[TYPE]:
            continue
        if not r[CAT] or (old is not None and r[CAT] == old[CAT]):
            continue
        t = normalize_type(r[TYPE])
        if r[CAT] not in CATEGORIES:
            out.errors.append(f"{s} : catégorie inconnue « {r[CAT]} » ({', '.join(CATEGORIES)})")
        elif not t:
            out.errors.append(f"{s} : une catégorie demande un type produit")
        elif cat_changes.get(t, r[CAT]) != r[CAT]:
            out.errors.append(f"type {t} classé à la fois « {cat_changes[t]} » et « {r[CAT]} »")
        else:
            cat_changes[t] = r[CAT]
    for t, c in cat_changes.items():
        if out.categories.get(t) != c:
            out.categories[t] = c
            out.changes.append(f"type {t} → catégorie « {c} »")
            out.touched.add("categories")

    # Multiple list : on garde chaque ligne d'origine, modifiée seulement si son SKU l'est.
    mult_fields = ((DESC, "description"), (TYPE, "type"), (MULT, "order_multiple"),
                   (PALLET, "units_per_pallet"))  # fmt: skip
    in_list = {r["sku"].strip() for r in src.multiples}
    multiples: list[Record] = []
    for rec in src.multiples:
        s = rec["sku"].strip()
        if s in removed:
            continue
        if s in changed:
            r, old = after[s], before[s]
            rec = dict(rec)
            for col, key in mult_fields:
                if r[col] != old[col]:
                    rec[key] = r[col]
        multiples.append(rec)
    for s in changed - in_list:
        r = after[s]
        if any(r[c] for c, _ in mult_fields if c != DESC) or (r[DESC] and s not in before):
            if s == NO_REPLACEMENT:
                continue
            multiples.append({"sku": s, **{k: r[c] for c, k in mult_fields}})
    for s in sorted(changed):
        old = before.get(s)
        diffs = [f"{c} {old[c] or '∅'} → {after[s][c] or '∅'}" for c in PRODUCT_COLUMNS
                 if old is not None and c != CAT and after[s][c] != old[c]]  # fmt: skip
        out.changes.append(f"{s} : " + (", ".join(diffs) if old is not None else "ajouté"))
    out.changes += [f"{s} : retiré" for s in removed]
    if multiples != src.multiples:
        out.touched.add("multiples")
    out.multiples_csv = _csv(MULTIPLE_COLUMNS, multiples)

    # Conversions : réécrites seulement pour les SKU dont « ancien SKU » ou la date change.
    conv_changed = {
        s for s in changed
        if s not in before or any(after[s][c] != before[s][c] for c in (OLD, CONV_DATE))
    }  # fmt: skip
    comments = {(c["old_sku"].strip(), c["new_sku"].strip()): c.get("comment", "")
                for c in src.conversions}  # fmt: skip
    conversions = [c for c in src.conversions
                   if c["new_sku"].strip() not in conv_changed | set(removed)]  # fmt: skip
    for s in sorted(conv_changed):
        old_skus, dates = _split(after[s][OLD]), _split(after[s][CONV_DATE])
        if old_skus and not dates:
            out.errors.append(f"{s} : date de conversion manquante")
            continue
        if len(dates) not in (1, len(old_skus)) and old_skus:
            out.errors.append(f"{s} : une date, ou une date par ancien SKU")
            continue
        for k, o in enumerate(old_skus):
            d = dates[0] if len(dates) == 1 else dates[k]
            conversions.append({"old_sku": o, "new_sku": s, "effective_date": d,
                                "comment": comments.get((o, s), "")})  # fmt: skip
    if conversions != src.conversions:
        out.touched.add("conversions")
    out.conversions_csv = _csv(CONVERSION_COLUMNS, conversions)

    # Exclusions : « oui » = toutes les boutiques, sinon les codes (B80;B1).
    exclusions: list[Record] = []
    old_excl = {e["sku"].strip(): e for e in src.exclusions}
    for e in src.exclusions:
        s = e["sku"].strip()
        if s in removed or (s in changed and (after[s][EXCL], after[s][EXCL_REASON])
                            != (before[s][EXCL], before[s][EXCL_REASON])):  # fmt: skip
            continue
        exclusions.append(e)
    for s in sorted(changed):
        r = after[s]
        prev = before.get(s)
        if prev is not None and (r[EXCL], r[EXCL_REASON]) == (prev[EXCL], prev[EXCL_REASON]):
            continue
        if not r[EXCL] or r[EXCL].lower() in ("non", "no", "n"):
            continue
        if len(r[EXCL_REASON]) < 3:
            out.errors.append(f"{s} : raison de l'exclusion obligatoire (3 caractères au moins)")
            continue
        boutiques = "ALL" if r[EXCL].lower() in YES else ";".join(
            b.upper() for b in _split(r[EXCL]))  # fmt: skip
        keep = old_excl.get(s)
        same = keep is not None and keep["reason"].strip() == r[EXCL_REASON]
        exclusions.append({
            "sku": s, "boutiques": boutiques, "reason": r[EXCL_REASON],
            "author": keep["author"] if same and keep else author,
            "updated_on": keep["updated_on"] if same and keep else today.isoformat(),
        })  # fmt: skip
    if exclusions != src.exclusions:
        out.touched.add("exclusions")
    out.exclusions_csv = _csv(EXCLUSION_COLUMNS, exclusions)
    return out
