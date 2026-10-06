"""Génère les modèles Excel vierges à remplir par l'équipe (dossier templates/).

python tools/make_templates.py
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "templates"

TEMPLATES = {
    "allocations.xlsx": (
        ["boutique", "sku", "total_qty", "already_sent", "wave_plan", "current_wave", "comment"],
        [
            "boutique : code boutique (ex. B80).",
            "sku : SKU en TEXTE (formater la colonne en Texte avant de coller).",
            "total_qty : quantité totale allouée à la boutique.",
            "already_sent : quantité déjà envoyée lors des vagues précédentes (0 sinon).",
            "wave_plan : pourcentages des vagues séparés par /, total 100 (ex. 100/0 ou 75/25).",
            "current_wave : numéro de la vague à envoyer maintenant (1, 2…).",
            "Quantité envoyée = total × % cumulé jusqu'à la vague en cours − déjà envoyé.",
            "Une allocation REMPLACE la prévision pour ce SKU (elle n'est jamais écrasée).",
        ],
    ),
    "launches.xlsx": (
        ["boutique", "sku", "launch_date", "qty", "comment"],
        [
            "launch_date : date de lancement (format AAAA-MM-JJ ou date Excel).",
            "qty : quantité initiale à avoir en boutique au lancement.",
            "Jusqu'à la date de lancement : commande = qty − Expected, arrondi au multiple.",
            "Après la date : la prévision normale reprend.",
        ],
    ),
    "target_stock.xlsx": (
        ["boutique", "sku", "min_qty", "target_qty", "max_qty"],
        [
            "Stock après commande (Expected + commande) comparé à min / cible / max.",
            "En dessous du min : on remonte à la cible. Au-dessus du max : on plafonne.",
            "Ne s'applique pas aux allocations ni aux lancements.",
        ],
    ),
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, (headers, notes) in TEMPLATES.items():
        wb = Workbook()
        ws = wb.active
        ws.title = "Données"
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="EFE6DC")
        for col in ("A", "B"):
            for row in range(2, 501):
                ws[f"{col}{row}"].number_format = "@"  # texte : protège les SKU
        for i, _ in enumerate(headers):
            ws.column_dimensions[chr(65 + i)].width = 16
        help_ws = wb.create_sheet("Mode d'emploi")
        for line in notes:
            help_ws.append([line])
        help_ws.column_dimensions["A"].width = 100
        wb.save(OUT / name)
        print(f"Modèle écrit : {OUT / name}")


if __name__ == "__main__":
    main()
