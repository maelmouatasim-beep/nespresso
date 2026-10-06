# Prompts Claude Code — Supply Planning Copilot

Mode d'emploi :

- Envoie un prompt par session Claude Code, dans l'ordre.
- Ne passe au suivant que quand les tests sont verts et que tu as vérifié le résultat.
- Le prompt 1 est prêt. Les suivants sont des ébauches qu'on affinera ensemble après chaque étape.

---

## Avant le prompt 1 (à faire toi-même, 10 min)

1. Crée un dépôt GitHub **privé** nommé `supply-planning-copilot`, avec un README.
2. Ajoute dans le dépôt :
   - `CLAUDE.md` à la racine
   - `docs/analyse_existant.md`
   - le dossier `tests/fixtures/b80_2026-10-05/` (garde le même chemin)
3. Ne mets **pas** les fichiers Excel réels (calculateur, Bible, exports) dans le dépôt.
4. Ouvre claude.ai/code, choisis ce dépôt et colle le prompt 1.

---

## PROMPT 1 — Fondations + moteur Forecast + parité Excel

```
Tu construis le "Supply Planning Copilot", un outil interne de planification des commandes boutiques Nespresso Canada.

Lis d'abord ENTIÈREMENT :
- CLAUDE.md (règles absolues, logique Excel vérifiée, vocabulaire)
- docs/analyse_existant.md (constats sur les fichiers réels)
- le contenu de tests/fixtures/b80_2026-10-05/ (inspecte chaque fichier : colonnes, types, exemples)

Je débute en programmation : explique ce que tu fais en termes simples, et termine en me donnant les commandes exactes pour vérifier moi-même.

OBJECTIF DE CETTE ÉTAPE
Poser les fondations du projet et reproduire EXACTEMENT la formule "Suggested Qty" du calculateur Excel, prouvée par des tests de parité. Pas d'interface, pas d'IA, pas de base de données à cette étape.

1. Structure du projet
- pyproject.toml (Python 3.12 ; deps : pandas, pydantic>=2, openpyxl ; dev : pytest, ruff)
- app/domain/      modèles pydantic : Product, StockSnapshotLine, StockMovement, SkuConversion, PlanningParameters, RecommendationLine (statut OK/REVIEW/BLOCKED, reasons: list[str], explanation: dict)
- app/engines/forecast.py   fonctions PURES, aucune I/O
- app/engines/conversion.py résolution old/new SKU
- app/ingestion/fixtures_loader.py  charge les CSV de fixtures (SKU toujours en str)
- tests/unit/, tests/parity/
- docs/decision_log.md, docs/open_questions.md, docs/data_dictionary.md
- .gitignore : data/, *.xlsx, *.xlsm, *.csv HORS tests/fixtures/, .env

2. Moteur forecast — deux modes explicites
a) mode="excel_parity" : reproduit à l'identique la logique décrite dans CLAUDE.md, section "Logique Excel VÉRIFIÉE", y compris :
   - cover café optionnel (seulement si product_type == "C")
   - CEILING au multiple, 0 si <= 0
   - multiple absent → 1 (comme Excel)
   - exception 7010.70 → 0
   - conversion : le new SKU additionne sales + expected de l'old SKU ; l'old SKU vaut 0 dès run_date >= effective_date + 1 jour
   - le SKU "XYZ123" = pas de remplaçant
b) mode="safe" (notre version cible) : même calcul, MAIS
   - multiple absent pour un café : inférer 240 si la description commence par "VER-", 800 si "ORI-" (voir CLAUDE.md, "Patterns métier"), calculer avec ce multiple, et marquer REVIEW "Multiple inféré depuis la famille, à confirmer"
   - multiple absent pour un non-café → REVIEW "Multiple manquant", qty calculée avec 1 mais affichée comme non fiable
   - café avec multiple == 1 dans la Multiple list → REVIEW "Multiple suspect"
   - sales = 0 et expected = 0 → pas de commande, statut OK, raison "Aucune activité"
   - expected négatif → REVIEW
   - DC stock < qty → REVIEW "Stock DC insuffisant" (sans plafonner)
   - old SKU bloqué → BLOCKED + raison qui indique le new SKU à commander
PlanningParameters : cover_days, coffee_cover_days (optionnel), run_date, boutique sont OBLIGATOIRES, sans valeur par défaut. Le planner les saisit à chaque run. Ne jamais lire les cover days depuis master_schedule.csv.
Chaque ligne retourne une explication structurée : inputs utilisés, cover appliqué, besoin brut avant arrondi, multiple, qty finale, current_cover_days, post_cover_days, règles déclenchées.

3. Calcul des ventes
- Fonction qui agrège les StockMovement par SKU sur la période.
- Paramètre included_movement_codes : None = tous les codes, c'est le défaut voulu (ventes ET déstockages comptent comme consommation). Garde-le configurable, mais ne propose pas d'exclusion.
- sales_history_days = nombre de dates distinctes présentes dans les mouvements (comme Excel).

4. Tests de parité (le plus important)
- tests/parity/test_b80_parity.py : recalcule les 461 lignes de golden_stock_cover_final_B80.csv à partir des fichiers BRUTS (stock_movements_B80.csv, stock_situation_B80_CY1.csv, master_multiples.csv, master_sku_conversions.csv, parameters.json). Ne lis PAS les colonnes de résultat du golden comme entrée.
- Pour les lignes excel_cell_is_formula == True (416 lignes) : la qty doit être STRICTEMENT égale à excel_suggested_qty. Objectif : 416/416.
- Pour les 45 overrides manuels : ne pas faire échouer le test. Produire un rapport (tests/parity/reports/overrides_B80.md) qui liste SKU, qty formule, qty planner, écart, type produit. Exemple attendu : 7005.70 Melozio, formule 3120 vs planner 3600.
- Vérifie aussi current_cover_days et post_cover_days (tolérance 1e-6) sur les lignes formule.
- Si une ligne ne matche pas : NE CORRIGE PAS le test pour le faire passer. Investigue, explique l'écart, consigne-le dans docs/decision_log.md.

5. Tests unitaires
Couvre au minimum : arrondi au multiple (cas exact, cas juste au-dessus), besoin négatif, sales = 0, multiple absent (2 modes, inférence VER→240 / ORI→800), café avec multiple 1, conversion active/inactive le jour J et J+1, SKU avec zéros initiaux ("0005452") et suffixes ("3517/BULK") conservés en texte, cover café vs non-café.

6. Fin de l'étape
- Lance ruff et pytest, et montre le résultat.
- Mets à jour docs/open_questions.md avec toute ambiguïté rencontrée.
- Fais un commit clair par sous-étape, puis pousse sur une branche "step-1-foundations" et ouvre une PR.
- Résume-moi en puces : ce qui est fait, le score de parité, les hypothèses, ce que je dois vérifier.

Contraintes : aucune quantité ne vient d'un LLM ; ne jamais convertir un SKU en nombre ; ne jamais committer de données réelles hors tests/fixtures.
```

---

## PROMPT 2 — Ingestion des vrais exports + validation (ébauche)

- Lecteurs robustes pour :
  - l'export Power BI **Stock Situation** (toutes boutiques)
  - l'export **Stock Movements**
  - les tables maîtres extraites du calculateur
- Validation de schéma, avec un rapport d'erreurs lisible : ligne « No filters applied », SKU non texte, doublons, Expected négatifs, types vides, fraîcheur des données (date d'extraction).
- Un script `tools/extract_masters.py` qui lit un calculateur `.xlsm` en local et produit les CSV maîtres (multiples, conversions, schedule, DC mapping, portfolio).
- ⚠️ Il me faut d'abord **un export Stock Movements brut** venant de Power BI.

## PROMPT 3 — Interface Streamlit v0 (ébauche)

- Écran 1 :
  - upload des fichiers
  - choix de la boutique (liste issue de DC Mapping et Schedule)
  - date de livraison
  - cover café et non-café
  - historique
  - mode excel_parity ou safe
- Écran 2 :
  - tableau de validation, avec filtres OK/REVIEW/BLOCKED/Conversion
  - colonne qty finale éditable, avec raison obligatoire
  - panneau « Pourquoi cette quantité ? »
- Écran 3 :
  - résumé (unités, palettes, lignes par statut)
  - export CSV `SKU;QTY` au format LT
  - export du recap

## PROMPT 4 — Audit, overrides, SQLite (ébauche)

- Table des runs : paramètres, versions des sources, hash des fichiers.
- Table des recommandations et table des overrides : qui, quand, avant/après, raison.
- Écran historique : comparer la commande d'aujourd'hui à la précédente pour la même boutique.

## PROMPT 5 — Launches, allocations (vagues), target stock (ébauche)

- Modèles de fichiers d'entrée simples, fournis en template Excel :
  - `launches.xlsx`
  - `allocations.xlsx` (SKU × boutique × total × déjà envoyé × vague 100/0, 75/25…)
  - `target_stock.xlsx` (min/cible/max par boutique)
- Arbitrage selon la hiérarchie du brief, avec des tests anti-double-comptage.

## PROMPT 6 — Lecture de la Bible (ébauche)

- Parser de la feuille du jour : section boutiques, section lancements, section conversions, avec détection par en-têtes.
- Les règles boutique en texte libre (sacs max, palettes max, « keep expected ~140K ») deviennent une table `boutique_rules` validée par un humain.
- Hors périmètre pour l'instant : on n'y touche que si l'équipe le demande, et jamais les onglets contacts.

## PROMPT 7 — Couche IA (ébauche)

- API Claude, seulement si l'IT l'approuve :
  - expliquer une ligne en langage clair
  - structurer une note libre en proposition (avec niveau de confiance, et validation humaine obligatoire)
  - résumer les anomalies du run
- Jamais de données personnelles dans les prompts.
