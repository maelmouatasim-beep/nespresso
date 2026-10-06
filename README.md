# Supply Planning Copilot

Outil interne de planification des commandes boutiques (Nespresso Canada).
Règles du projet : voir [CLAUDE.md](CLAUDE.md).

## Démarrage le plus simple

- **Windows** : double-clique sur `lancer_outil.bat`.
- **Mac** : double-clique sur `lancer_outil.command` (la première fois : clic droit → Ouvrir).

La première fois, l'installation prend quelques minutes. Ensuite l'outil s'ouvre dans le navigateur.
Il faut Python 3.12 (https://www.python.org/downloads/, cocher « Add python.exe to PATH » sur Windows).

## Installation manuelle (une fois)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Utiliser l'outil

```bash
streamlit run app/ui/main.py
```

Le navigateur s'ouvre sur l'outil. Ensuite :

1. **Barre de gauche** : tape ton nom, puis « Charger la démo » (données de test B80) ou « Mes fichiers »
   (exports Power BI + tables maîtres).
2. **Onglet 1. Paramètres** : boutique, date de livraison, jours de couverture (et cover café si besoin),
   mode, puis « Calculer la commande ».
3. **Onglet 2. Validation** : filtre, vérifie les lignes REVIEW / BLOCKED, modifie une « Qty finale »
   avec une raison, puis « Appliquer ». En bas : « Pourquoi cette quantité ? ».
4. **Onglet 3. Résumé & export** : unités, palettes, signalements ; télécharge le fichier LT (Nessoft)
   et le récapitulatif boutique. Le run est enregistré dans `data/copilot.db`.
5. **Onglet Journée** : les boutiques qui commandent ce jour-là (d'après le Schedule) ; saisis le cover
   de chacune, calcule tout, télécharge le zip des fichiers LT, ou ouvre une boutique pour la valider.
6. **Onglet Historique & overrides** : runs passés, comparaison avec la commande en cours, et analyse
   des modifications des planners (par catégorie, par SKU).

Chaque modification de quantité exige une **catégorie** (promo, lancement, rupture…), une **raison**
et ton **nom**. Dans l'onglet 3, « Télécharger le détail (Excel) » donne toute la commande avec ses
explications, les paramètres et les modifications.

### Préparer les vrais fichiers (en local, jamais dans le dépôt)

```bash
python tools/extract_masters.py "data/B80 105.xlsm" --inspect           # voir les onglets
python tools/extract_masters.py "data/B80 105.xlsm" --boutique B80     # écrit data/masters/*.csv
python tools/make_templates.py                                          # modèles vierges dans templates/
```

Modèles à remplir (dossier `templates/`) : `allocations.xlsx`, `launches.xlsx`, `target_stock.xlsx`,
`boutique_rules.xlsx` (règles tirées des notes de la Bible, validées par un humain : palettes max,
quantité max / min par SKU…). Une règle ne change jamais une quantité : elle signale.

```bash
```

## Vérifier

```bash
pytest                       # tous les tests
pytest tests/parity -v -s    # parité avec Excel (affiche le score)
ruff check . && ruff format --check .
mypy                         # types des moteurs et du domaine
```

## Structure

- `app/domain/` : modèles de données (SKU toujours en texte)
- `app/engines/` : calculs purs (ventes, conversions, quantités)
- `app/ingestion/` : lecture des fichiers (exports Power BI, tables maîtres, modèles) + rapports de validation
- `app/services/` : enchaînement complet d'un run
- `app/storage/` : base SQLite locale (runs, recommandations, overrides)
- `app/ui/` : interface Streamlit
- `tools/` : extraction du calculateur, modèles Excel, aperçu HTML
- `templates/` : modèles vierges allocations / lancements / stock cible
- `tests/unit/`, `tests/parity/` : tests ; rapport des overrides dans `tests/parity/reports/`
- `docs/` : analyse, journal des décisions, questions ouvertes, dictionnaire de données

## Aperçu de test (HTML)

```bash
python tools/build_preview.py   # écrit preview/apercu_B80.html, à ouvrir dans un navigateur
python tools/build_demo_app.py  # écrit preview/outil_B80.html : version web complète de la démo
```

Les quantités de l'aperçu sont pré-calculées par le moteur Python ; la page ne fait que les afficher.
Le dossier `preview/` n'est pas versionné.
