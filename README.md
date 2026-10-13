# Boutique Supply Planner

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

Le navigateur s'ouvre sur l'outil. Dans la barre de gauche : choisis ton prénom (« Ajouter un planner »
la première fois, prénom seulement), choisis « Mes données »
(ou « Démo B80 » pour essayer), puis suis les écrans dans l'ordre :

1. **Dépôt du jour** : choisis « Mes données », puis glisse les deux exports Power BI du matin
   (Stock Situation et Stock Movements) dans la zone de dépôt, ensemble ou l'un après l'autre.
   L'outil reconnaît chaque fichier par ses colonnes, lit la date d'extraction et affiche sa
   fraîcheur (vert = aujourd'hui, orange = hier, rouge = plus vieux), puis les contrôles qualité.
   La première fois, importe les référentiels depuis ton calculateur Excel (`.xlsm`) ou pars des
   référentiels de test. Clique sur « Prêt ».
   L'outil ne se connecte jamais à Power BI : c'est toi qui déposes les fichiers.
2. **Commandes** : ajoute une boutique (recherche par code ou nom ; rien n'est présélectionné). Une
   boutique = une commande simple, plusieurs = une journée. Chaque boutique a sa ligne de paramètres
   (date de livraison, cover non-café, cover café facultatif, jours d'historique, mode) ; les
   paramètres de la première sont proposés aux suivantes. « Calculer » par boutique ou « Tout
   calculer ». Chaque boutique est un onglet (code, statut à calculer / calculé / confirmé, REVIEW et
   BLOCKED restants, croix pour la retirer) ; une barre récapitulative donne unités, palettes et
   lignes à traiter par boutique et au total. Un clic sur l'onglet affiche sa table. Toutes les lignes sont dans une seule
   grille, comme dans le calculateur Excel : filtres Catégorie et Statut, « Qty > 0 seulement »,
   recherche, tri au clic sur un en-tête. Coche 🔍 sur une ligne pour voir « Pourquoi cette quantité ? »
   à droite. Tape la « Qty finale » directement dans la cellule : elle compte tout de suite (totaux,
   palettes, export). Le statut de la ligne ne change pas : la cellule devient bleue et un marqueur
   « modifié » s'ajoute à côté du statut. Rien n'est demandé : l'outil garde l'ancienne
   et la nouvelle qty, ton prénom, l'heure et un contexte déduit (événement actif, stock DC
   insuffisant, SKU dormant ou « non précisé »), visible dans « Pourquoi » et l'Historique. Une qty
   qui n'est pas un multiple est arrondie au multiple supérieur, avec un petit avertissement. Les SKU concernés par
   un événement ont une puce dans la colonne « Événement » (● équipe, ○ personnel) ; le bandeau
   « Événements actifs » filtre la table d'un clic.
   Confirmer est une action à part : coche « Confirmé » en début de ligne (la ligne passe en
   « Confirmé », décocher la remet à traiter), ou « Accepter toutes les lignes OK », ou
   « Confirmer les lignes affichées » pour traiter un paquet filtré. Filtres de statut : Tous,
   À traiter (OK et REVIEW non confirmés), REVIEW, BLOCKED, Confirmé, Modifié. La ligne ne bouge
   pas pendant la saisie : le tri ne se refait qu'au clic sur un en-tête ou au changement de filtre.
   Sous la table : « Télécharger le LT » de la boutique affichée et « Télécharger tous les LT (zip) ».
   Chacun passe par le garde-fou : toutes les lignes REVIEW et BLOCKED confirmées, et la vérification
   en 3 points des événements (lignes bloquées par un événement, SKU flagués non vus, lancements et
   demandes boutique à inclure) verte ; chaque validation est tracée. « Marquer comme vus » ne
   s'active qu'après avoir ouvert une fois le filtre « Événements » de la commande. Le zip attend que
   chaque boutique soit prête et dit lesquelles bloquent encore.
   L'outil n'écrit jamais dans Nessoft : tu importes le fichier toi-même.
3. **Export** : récapitulatif boutique, export Excel détaillé, « Enregistrer sans exporter ».
4. **Événements SKU** : promotions, lancements, arrêts, interdictions, conversions, urgences,
   demandes boutique et SKU à surveiller. Onglets « Équipe » et « Mes événements », table triable,
   panneau à droite pour créer, modifier, dupliquer, terminer. « + Demande boutique » : formulaire
   court (boutique, SKU, quantité, qui a demandé). Import / export Excel avec
   `templates/evenements_sku.xlsx` (exemples fictifs à remplacer).
5. **Référentiels** : multiples, conversions, portfolio, boutiques/DC, Schedule, exclusions,
   catégories (type produit → catégorie du filtre), allocations, lancements, stocks cibles, règles boutique. Propriétaire, date de mise à jour,
   anomalies, import depuis un modèle.
6. **Historique** : commandes enregistrées, modifications par contexte déduit, SKU les plus modifiés,
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

Version web : « Démo B80 » montre des quantités pré-calculées ; « Mes fichiers » lit tes exports
dans le navigateur et les calcule avec le même moteur Python (Pyodide), sans rien envoyer.
Avant de générer la page la première fois : `python tools/fetch_pyodide.py`.
Le dossier `preview/` n'est pas versionné.
