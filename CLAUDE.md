# CLAUDE.md — Supply Planning Copilot

## Contexte

- Outil interne pour l'équipe Boutique Planning de Nespresso Canada (~35 boutiques, 4 DC : MT1, TO1, CY1, VA1).
- Il remplace progressivement le calculateur Excel « B80 105.xlsm » (un fichier par boutique) et une partie du travail manuel fait à partir de la « Bible » (Daily Orders 2026.xlsx).
- Utilisateur : un planner. Il choisit la boutique, la date de livraison et les jours de couverture. L'outil génère une commande justifiée, ligne par ligne.
- Le propriétaire du projet débute en programmation. Il faut expliquer simplement, donner les commandes exactes à lancer et éviter le jargon inutile.

## Règles absolues (ne jamais enfreindre)

1. **Aucune quantité n'est produite par un LLM.** Le moteur de quantités est du Python déterministe, pur, testé et versionné. L'IA sert seulement à expliquer, structurer des notes et signaler des anomalies.
2. **Les SKU sont toujours des chaînes de caractères** (`"7005.70"`, `"0005452"`, `"3517/BULK"`). Ne jamais les convertir en nombre. Toute lecture Excel/CSV force `dtype=str` sur les colonnes SKU.
3. **Rien de silencieux.** Une donnée manquante ou ambiguë produit `REVIEW` ou `BLOCKED` avec une raison. Ne jamais remplacer silencieusement une valeur par 0 ou 1.
4. Chaque quantité recommandée garde sa trace : sources, version de règle, paramètres et calcul intermédiaire.
5. Une allocation officielle n'est jamais écrasée par la prévision.
6. Un override du planner garde toujours son auteur et son horodatage. La catégorie et la raison sont facultatives (décision du planner, 08-oct-2026, D-028).
7. Aucune écriture dans Nessoft. L'outil produit uniquement un fichier d'export.
8. **Données confidentielles.** Ne jamais committer de fichiers Excel réels, d'exports Power BI ni de données personnelles (noms, emails, contacts boutiques). `data/` est dans `.gitignore`. Seul `tests/fixtures/` (données de test approuvées) est versionné.
9. Ne jamais supposer la structure d'un fichier. L'inspecter, puis valider le schéma à l'ingestion.
10. Ne jamais masquer un écart de parité avec Excel. Le documenter dans `docs/decision_log.md`.

## Stack

- Python 3.12, pandas, pydantic v2, pytest, openpyxl
- Interface : Streamlit (MVP)
- Stockage : SQLite (audit, runs, overrides), plus tard
- Qualité : ruff (lint + format), mypy recommandé sur `app/engines` et `app/domain`

## Commandes

```bash
pip install -e ".[dev]"     # installer
pytest                       # tous les tests
pytest tests/parity -v       # tests de parité Excel
ruff check . && ruff format .
mypy                         # types (app/engines, app/domain)
streamlit run app/ui/main.py # lancer l'interface
```

## Logique Excel VÉRIFIÉE (Stock Cover Final, B80, 05-oct-2026)

Reproduite à l'identique sur 416/416 lignes calculées par formule. Les 45 autres lignes sont des overrides manuels.

Paramètres (cellules S3:S8) :

- `cover_days` (S4), par exemple 9
- `coffee_cover_days` (S6) : optionnel, s'applique seulement si `product_type == "C"`
- `sales_history_days` (S5) = nombre de dates distinctes dans les ventes, par exemple 7
- `sales_days_until_delivery` (S3) = S4 − 3. **Il n'est utilisé que pour l'affichage « Closing stk », pas dans la quantité.**

Par SKU :

```
sales      = Σ quantity des mouvements du SKU sur la période (TOUS codes de mouvement confondus)
expected   = Expected de la Stock Situation de la boutique
           (vérifié : Expected = Available + Incoming − Waiting ; Reserved n'est PAS déduit)
multiple   = order_multiple de la Multiple list ; si absent → 1 (comportement Excel, à transformer en REVIEW chez nous)
cover      = coffee_cover_days si (coffee_cover_days défini et type == "C") sinon cover_days

si sku == "7010.70"                                   → 0 (exception codée en dur ; chez nous : référentiel Exclusions)
si sku est un OLD SKU et run_date >= effective_date+1 → 0 (ancien SKU bloqué)
si sku est un NEW SKU (conversion) :
    sales    += sales(old_sku)
    expected += expected(old_sku)

raw  = cover * sales / sales_history_days − expected
qty  = CEILING(raw, multiple) si raw > 0 sinon 0

current_cover_days = expected / sales * sales_history_days
post_cover_days    = (expected + qty) / sales * sales_history_days
dc_stock           = Available du SKU dans le DC de la boutique (DC Mapping) ; "INSUFFICIENT" si < qty (signalé, pas plafonné)
```

Autres règles Excel :

- `Filter out` : la ligne est retirée si le SKU est dans Boutique Portfolio avec `Yes`, ou s'il est absent du portfolio et que `expected + sales == 0`. Excel compare les SKU en **numérique puis en texte** (`7005.7` = `7005.70`) : c'est ce qui donne les 451 lignes de B80.
- Le SKU `XYZ123` dans SKU Conversion signifie « pas de remplaçant, ne plus envoyer ».
- Export LT (Nessoft) : CSV à une colonne, une ligne `SKU;QTY` par SKU avec qty > 0, nommé `"{BTQ} {dd-Mmm-yyyy-hh_mm}.csv"`.
- Palettes : VL caps / 8000 (8640 si CY1), OL caps / 24000 (28800 si CY1), machines / 20 (24 si CY1), autres = Σ qty / units_per_pallet.

## Patterns métier à reconnaître (confirmés par le planner)

- **Cafés Vertuo (description `VER-…`) : multiple 240.** Exception : certains espressos Vertuo (Altissio, Voltesso, Diavolitto, Orafio, Altissio Decaf) sont à 360.
- **Cafés Original (description `ORI-…`, aussi appelés OL) : multiple 800.** Certains DC acceptent 200/400/600, mais 800 reste le standard.
- Si un café n'a pas de multiple dans la Multiple list : inférer 240 (VER) ou 800 (ORI) à partir de la description, et marquer la ligne `REVIEW` avec la raison « multiple inféré depuis la famille, à confirmer ». Jamais 1 en silence.
- La Multiple list contient beaucoup d'entrées à `1` pour des cafés : c'est suspect. Un café avec multiple 1 doit aussi passer en `REVIEW`.
- Les jours de couverture **changent chaque jour** et sont décidés par le planner. L'outil les exige en entrée à chaque run, sans valeur par défaut, et n'utilise jamais les cover days de l'onglet Schedule ni de la Bible (ils ne sont pas à jour).
- Les ventes viennent de Power BI (Stock Movements) et incluent volontairement tout ce qui sort du stock boutique : ventes et déstockages. Par défaut, tous les codes de mouvement sont inclus. La liste reste configurable, mais ce n'est pas une décision à prendre maintenant.
- L'outil est destiné à toute l'équipe de planners, pas à une seule personne.

Décisions du planner (étape 2, 07-oct-2026) :

- **Dépôt du jour, pas de connexion Power BI (A1).** Le planner dépose chaque matin deux exports (Stock Situation, Stock Movements). L'outil détecte la date d'extraction (nom du fichier, sinon propriétés du classeur ; sinon il la demande) et affiche la fraîcheur : vert = aujourd'hui, orange = hier, rouge = plus vieux. Fichiers gardés dans `data/depot/`, jamais versionnés. Rien ne doit laisser croire à une connexion automatique.
- **Toutes les boutiques sont toujours sélectionnables (A2)**, avec recherche par code ou nom. Le Schedule ne filtre jamais : il présélectionne (Journée) et suggère une date de livraison, que le planner saisit ou confirme toujours.
- **Arrondi au multiple supérieur toujours, pour toutes les sources (A3)** : prévision, allocation, lancement, stock cible, override. Café sans multiple → 240 (VER) / 800 (ORI) + `REVIEW` « multiple inféré » ; autre produit sans multiple → `REVIEW` « multiple manquant » ; café à multiple 1 → `REVIEW` « multiple suspect ».
- **Portfolio comparé en numérique puis en texte (A4)**, comme Excel (451 lignes sur B80).
- **Périmètre = tous les SKU avec une activité (A5)** : dans le portfolio, OU Expected/Available/Incoming non nul, OU un mouvement dans la fenêtre. Rien n'est caché ; les lignes à 0 sans alerte sont seulement repliées. Le mode « Parité Excel » garde la sélection Excel.
- **SKU dormant / retour en stock DC (A6).** Expected 0, ventes 0, stock DC > 0 et (dans le portfolio OU ventes sur l'historique long) → `REVIEW` « Retour en stock DC : rupture probable, à réapprovisionner », quantité suggérée = cover × moyenne journalière de l'historique de secours, arrondie au multiple. Même cas avec stock DC 0 → `OK` « Rupture DC, rien à envoyer ».
- **Historique (A7).** L'export de mouvements peut couvrir plusieurs semaines. La fenêtre de la formule reste au choix du planner (défaut 7 jours). L'historique de secours = moyenne journalière sur les X dernières semaines complètes avec ventes (défaut 4), utilisé seulement pour les SKU dormants et toujours cité dans le « Pourquoi ».
- **Exclusions (A8).** Plus aucun SKU codé en dur (ex. `7010.70`) : un référentiel éditable « Exclusions » (SKU, boutiques ou toutes, raison, auteur, date) bloque la ligne (`BLOCKED`, quantité 0) et la raison apparaît dans le « Pourquoi ».
- **Doublons de multiples (A9).** `7922.70` est un café ORI → 800. Quand un SKU a deux multiples différents, la ligne passe en `REVIEW` « Deux multiples dans la Multiple list » ; en mode standard, le multiple de la famille (240 VER / 800 ORI) l'emporte s'il fait partie des deux. Les autres doublons restent une question ouverte.
- **Catégories d'affichage (étape 2 bis).** Le filtre « Catégorie » de la commande classe les SKU par type produit : Cafés = C ; Machines = M ; Consommables & sacs = A, AP, D, FB, PA, PM, MD ; Accessoires = LC, CH, GC, T, TX, F ; tout le reste = Autres. Cafés Vertuo et Original restent ensemble. Cette table est un référentiel modifiable (Référentiels → Catégories) ; elle ne change aucune quantité.
- **Exports Power BI réels (vérifiés le 07-oct-2026)** : un seul onglet `Export`, toutes les boutiques et les DC, nom « data - AAAA-MM-JJT….xlsx ». Stock Movements : colonnes « Stock Movement Id, Stock, Product Nr, Product Type (Prod), Stock Mvt Date, Mvt Code, Quantity (Sum), Mvt Code Descr (Mvt Cd) », sorties **en négatif** → comptées en ventes positives. Les fichiers sont reconnus par leurs colonnes, pas par leur nom.
- **Écran Commande = une seule grille, comme le calculateur Excel** : pas de blocs ni de sections repliées ; filtres Catégorie / Statut / Qty > 0 / recherche sans recalcul ; tri au clic sur chaque en-tête ; tri par défaut REVIEW et BLOCKED en haut puis Qty proposée décroissante ; panneau « Pourquoi » à droite, sans quitter la table.

## Charte visuelle

- Thème CLAIR uniquement (outil installé ET démo web), palette Nespresso : fond crème `#FAF7F2`, cartes blanches, texte `#2B1D14`, espresso `#3D2B1F` (barre latérale, en-têtes), accent doré `#B08D57` (boutons, survol), bordures `#E8E1D8`. Statuts pastille + texte : OK vert sauge, REVIEW ambre, BLOCKED rouge brique, Modifié bleu. Détails et contrastes vérifiés : `docs/charte_visuelle.md`.

## Périmètre des sources

- Le calculateur `B80 105.xlsm` et le brief sont les deux sources de vérité.
- La Bible (`Daily Orders 2026.xlsx`) a servi uniquement à comprendre le mode de travail. On n'en extrait **aucun** contact, nom, email, ni donnée personnelle, et on ne la parse pas pour l'instant.
- Projet expérimental, sans validation IT à ce stade : tout tourne en local, aucune donnée réelle n'est envoyée à un service externe ni à un LLM, et le dépôt ne contient que les fixtures approuvées.

## Vocabulaire métier

- BTQ = boutique. DC = centre de distribution (MT1 Montréal, TO1 Toronto, CY1 Calgary, VA1 Vancouver).
- VL / VER = Vertuo, OL / ORI = Original. Types produit : C café, M machine, LC/A/AP/D/FB/… accessoires, consommables et documents.
- Expected = stock attendu (dispo + entrant − en attente). Cover days = jours de couverture.
- LT = fichier d'upload vers Nessoft (ERP). Bible = Daily Orders 2026.xlsx.
- Launch = lancement produit. Allocation = quantité imposée par le business. Conversion = old SKU → new SKU.

## Façon de travailler

- Avant un changement important, résumer en 3 à 5 puces ce qui a été compris et ce qui va être fait.
- Petits commits, chacun avec ses tests. Toujours lancer `pytest` avant de dire « terminé ».
- Toute hypothèse métier non confirmée va dans `docs/open_questions.md`. Ne pas la coder en dur sans drapeau.
- Moteurs = fonctions pures (entrées → résultat + explication). Aucune I/O dans `app/engines/`.
