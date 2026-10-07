"""Anomalies des référentiels, affichées à l'écran Référentiels. Aucun calcul de quantité."""

from __future__ import annotations

from collections import Counter

from app.domain.models import COFFEE_TYPE, Exclusion
from app.engines.conversion import ConversionError, build_conversion_index
from app.engines.portfolio import numeric_key
from app.ingestion.masters import Masters


def multiples_anomalies(masters: Masters) -> list[str]:
    out = []
    conflicts = [p for p in masters.products.values() if p.alt_multiples]
    for p in conflicts:
        listed = " / ".join(str(m) for m in sorted({p.order_multiple or 0, *p.alt_multiples}))
        out.append(f"{p.sku} : deux multiples différents ({listed})")
    doubles = sorted({a for a in masters.product_list_anomalies if "espaces" not in a})
    padded = [a for a in masters.product_list_anomalies if "espaces" in a]
    if doubles:
        out.append(f"{len(doubles)} SKU en double (première ligne utilisée), ex. {doubles[:6]}")
    out += [f"SKU mal formé : {a}" for a in padded]
    zero = [p.sku for p in masters.products.values() if p.order_multiple == 0]
    if zero:
        out.append(f"{len(zero)} SKU avec multiple 0 : {zero[:8]}")
    coffee_one = [
        p.sku
        for p in masters.products.values()
        if p.product_type == COFFEE_TYPE and p.order_multiple == 1
    ]
    if coffee_one:
        out.append(f"{len(coffee_one)} café(s) avec multiple 1 (suspect) : {coffee_one[:8]}")
    return out


def conversions_anomalies(masters: Masters) -> list[str]:
    try:
        build_conversion_index(masters.conversions)
    except ConversionError as exc:
        return [str(exc)]
    return []


def portfolio_anomalies(masters: Masters) -> list[str]:
    out = []
    for boutique, entries in masters.portfolio.items():
        bad = [s for s, f in entries.items() if f.strip().lower() not in ("yes", "no")]
        if bad:
            out.append(f"{boutique} : indicateur autre que Yes/No pour {bad[:6]}")
        keys = Counter(numeric_key(s) or s for s in entries)
        dup = [k for k, n in keys.items() if n > 1]
        if dup:
            out.append(f"{boutique} : SKU en double (même valeur numérique) {dup[:6]}")
    return out


def dc_mapping_anomalies(masters: Masters) -> list[str]:
    known = {"MT1", "TO1", "CY1", "VA1"}
    odd = sorted({dc for dc in masters.dc_mapping.values() if dc not in known})
    return [f"DC inconnu(s) : {odd}"] if odd else []


def exclusions_anomalies(exclusions: list[Exclusion], masters: Masters) -> list[str]:
    unknown = [e.sku for e in exclusions if e.sku not in masters.products]
    return [f"SKU absent de la Multiple list : {unknown}"] if unknown else []
