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

Le navigateur s'ouvre sur l'outil. Dans la barre de gauche : tape ton nom, choisis « Mes données »
(ou « Démo B80 » pour essayer), puis suis les écrans dans l'ordre :

1. **Dépôt du jour** : dépose les deux exports Power BI du matin (Stock Situation et Stock Movements).
   L'outil lit la date d'extraction et affiche sa fraîcheur (vert = aujourd'hui, orange = hier,
   rouge = plus vieux), puis les contrôles qualité. Clique sur « Prêt ».
   L'outil ne se connecte jamais à Power BI : c'est toi qui déposes les fichiers.
2. **Commande boutique** : boutique (recherche par code ou nom), date de livraison, cover, cover café
   (facultatif), jours d'historique, mode, puis « Calculer ». Le tableau est groupé par famille, les
   exceptions d'abord. Clique sur une ligne pour voir « Pourquoi cette quantité ? ». Une modification
   de quantité demande une catégorie et une raison.
3. **Journée** : plusieurs boutiques d'un coup (présélection du Schedule, modifiable), un cover par
   boutique, puis le zip des fichiers LT.
4. **Export** : fichier LT pour Nessoft, récapitulatif boutique, export Excel détaillé.
   L'outil n'écrit jamais dans Nessoft : tu importes le fichier toi-même.
5. **Référentiels** : multiples, conversions, portfolio, boutiques/DC, Schedule, exclusions,
   allocations, lancements, stocks cibles, règles boutique. Propriétaire, date de mise à jour,
   anomalies, import depuis un modèle.
6. **Historique** : commandes enregistrées, modifications par catégorie, SKU les plus modifiés,
   comparaison de deux commandes.

Les fichiers déposés et la base locale restent dans `data/` (jamais versionné).

### Préparer les vrais fichiers (en local, jamais dans le dépôt)

```bash
python tools/extract_masters.py "data/B80 105.xlsm" --inspect           # voir les onglets
python tools/extract_masters.py "data/B80 105.xlsm" --boutique B80     # écrit data/masters/*.csv
python tools/make_templates.py                                          # modèles vierges dans templates/
```

Modèles à remplir (dossier `templates/`) : `allocations.xlsx`, `launches.xlsx`, `target_stock.xlsx`,
`boutique_rules.xlsx` (règles tirées des notes de la Bible, validées par un humain : palettes max,
quantité max / min par SKU…). Une règle ne change jamais une quantité : elle signale.

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
