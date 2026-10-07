"""Contenu des fichiers d'export. Fonctions pures : elles retournent du texte,
l'écriture sur disque ou le téléchargement se font ailleurs.

Aucune écriture dans Nessoft (règle 7) : on produit seulement le fichier LT.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import datetime

from app.domain.models import RecommendationLine
from app.engines.pallets import PalletEstimate, family

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def lt_filename(boutique: str, when: datetime) -> str:
    """Nom Excel : « B80 05-Oct-2026-11_51.csv » (mois en anglais, indépendant de la langue)."""
    return f"{boutique} {when:%d}-{MONTHS[when.month - 1]}-{when:%Y-%H_%M}.csv"


def lt_lines(lines: Iterable[RecommendationLine]) -> list[str]:
    """Une ligne « SKU;QTY » par SKU avec qty > 0, dans l'ordre reçu."""
    return [f"{line.sku};{line.qty}" for line in lines if line.qty > 0]


def lt_content(lines: Iterable[RecommendationLine]) -> str:
    """Fichier LT : une seule colonne, une ligne `SKU;QTY` par SKU commandé."""
    rows = lt_lines(lines)
    return "".join(f"{row}\r\n" for row in rows)


def recap_csv(
    boutique: str,
    delivery: str,
    lines: Iterable[RecommendationLine],
    pallets: PalletEstimate,
) -> str:
    """Récapitulatif lisible (envoyé à la boutique) : lignes commandées + palettes."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Boutique", boutique, "Livraison", delivery])
    w.writerow([])
    w.writerow(["SKU", "Description", "Type", "Famille", "Quantité", "Origine"])
    for line in lines:
        if line.qty > 0:
            w.writerow(
                [
                    line.sku,
                    line.description or "",
                    line.product_type or "",
                    family(line),
                    line.qty,
                    line.source.value,
                ]
            )
    w.writerow([])
    w.writerow(["Famille", "Unités", "Palettes estimées"])
    for fam, units in pallets.units.items():
        w.writerow([fam, units, f"{pallets.pallets[fam]:.2f}"])
    w.writerow(["Total", sum(pallets.units.values()), f"{pallets.total_pallets:.2f}"])
    if pallets.skus_without_pallet_size:
        w.writerow(["Sans taille de palette", ", ".join(pallets.skus_without_pallet_size)])
    return buf.getvalue()
