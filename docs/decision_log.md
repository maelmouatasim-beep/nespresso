# Journal des décisions

Chaque décision ou écart de parité avec Excel est consigné ici (CLAUDE.md, règle 10).
Format : contexte → décision → conséquence.

---

## D-001 — Deux modes, mêmes signalements (2026-10-06)

- **Contexte** : le prompt 1 demande un mode `excel_parity` (identique à Excel) et un mode `safe` (garde-fous).
- **Décision** : les signalements (REVIEW / BLOCKED + raison) sont produits **dans les deux modes**. Le mode ne change que la quantité, et seulement dans 2 cas :
  - café sans multiple : `excel_parity` → 1 (comme Excel) ; `safe` → 240 (VER-), 360 (espressos Vertuo listés) ou 800 (ORI-) ;
  - multiple à 0 : `excel_parity` → qty 0 (CEILING(x; 0) = 0 dans Excel) ; `safe` → traité comme un multiple manquant.
- **Pourquoi** : la règle 3 (« rien de silencieux ») s'applique aussi en mode parité. Un planner qui travaille en mode parité doit quand même voir qu'un multiple manque.
- **Conséquence** : en mode `excel_parity`, une ligne peut être en REVIEW tout en ayant exactement la quantité d'Excel.

## D-002 — Calcul en fractions exactes (2026-10-06)

- **Décision** : `raw = cover × ventes / jours − Expected` est calculé avec `fractions.Fraction`, pas en virgule flottante.
- **Pourquoi** : quand le besoin tombe pile sur un multiple (ex. 480), une erreur d'arrondi flottant (480,0000000001) ferait monter au multiple suivant.
- **Résultat** : 416/416 quantités identiques à Excel sur B80.

## D-003 — Conversions : Excel affiche les valeurs du new SKU seul (2026-10-06)

- **Constat** : pour 3 new SKU dont l'old SKU a encore de l'activité, les colonnes affichées d'Excel (Sales, Expected, Current/Post cover days) montrent les valeurs du **new SKU seul** :
  - `7934.70` Congo : Excel affiche ventes 170 ; old `7872.70` a 120 de ventes → notre total 290 ;
  - `7947.70` Shanghai Lungo : Excel affiche Expected 660 ; old `7878.70` a 30 → notre total 690 ;
  - `7961.70` Capriccio : Excel affiche Expected 1060 ; old `7866.70` a 10 → notre total 1070.
- **Quantités** : identiques (0 dans les 3 cas, le besoin est négatif avec ou sans l'old SKU). Les données B80 ne permettent donc **pas** de prouver si la formule Qty d'Excel additionne l'old SKU. On suit CLAUDE.md (« sales += sales(old_sku) ; expected += expected(old_sku) »).
- **Décision** : notre moteur affiche les **totaux** (ceux qui servent à la quantité), et garde le détail old/new dans `explanation.inputs` (`sales_own`, `sales_from_old_skus`, etc.).
- **Test** : `KNOWN_CONVERSION_DISPLAY_DIVERGENCES` dans `tests/parity/test_b80_parity.py`. Le test vérifie que l'écart existe toujours et qu'Excel affiche exactement les valeurs propres du new SKU.
- **À confirmer** : voir open_questions Q-003.

## D-004 — Stock DC : extractions à des moments différents (2026-10-06)

- **Constat** : sur 416 lignes formule, 396 ont un stock DC (CY1) identique entre Excel et l'export Power BI fourni. 20 lignes ont un stock Excel **supérieur** de 1 à 150 unités (ex. 7984.70 : +150 ; 7072.70 : +80).
- **Explication probable** : le stock DC du calculateur vient d'une extraction faite à un autre moment que l'export du 05-oct 08:20 ; entre les deux, le DC a expédié des unités. Le stock boutique (Expected), lui, concorde à 100 %.
- **Impact** : aucun sur la quantité (le stock DC n'est jamais utilisé pour plafonner). Il peut changer le signalement « Stock DC insuffisant ».
- **Test** : `KNOWN_DC_STOCK_DIVERGENCES` (liste explicite des 20 SKU, écart vérifié entre +1 et +150).
- **SKU absent du DC** : Excel affiche 0 ; notre moteur garde « inconnu » (`None`) et signale REVIEW seulement si on commande.

## D-005 — Multiple list : doublons, espaces, zéros (2026-10-06)

- **Doublons** (34 SKU) : la **première** ligne gagne, comme RECHERCHEV. Un seul doublon a des multiples différents : `7922.70` (800 puis 240) → 800.
- **SKU avec espaces** (`' 473ECO/B'`, `' MA-TAG-EN'`, `'ICE-179  '`) : RECHERCHEV ne les trouve jamais. Une ligne propre du même SKU a donc toujours priorité. Ces lignes sont listées dans `product_list_anomalies`.
- **Multiple = 0** (9 SKU, types RM/M/P/T, aucun dans le golden B80) : voir D-001.

## D-006 — Overrides du planner : rapport, pas d'échec (2026-10-06)

- 45 lignes sur 461 ont une valeur tapée à la main dans Excel. Elles ne font pas échouer la parité. Le rapport `tests/parity/reports/overrides_B80.md` est régénéré à chaque `pytest`.
- Exemple : `7005.70` Melozio, formule 3120, planner 3600.

## D-007 — Liste des lignes : reprise du golden à l'étape 1 (2026-10-06)

- La règle « Filter out » décrite dans CLAUDE.md ne reproduit pas la liste des 461 lignes du golden (voir Q-002). À l'étape 1, le test de parité calcule donc les SKU **listés dans le golden** (seule la liste des SKU est reprise, aucune valeur de résultat). La sélection des lignes sera traitée à une étape ultérieure.

## D-008 — `7010.70` : BLOCKED (2026-10-06)

- Exception codée en dur dans Excel (toujours 0). On la reproduit dans les deux modes, avec le statut BLOCKED et une raison explicite, car la raison métier est inconnue (Q-001).

## D-009 — Old SKU encore commandable : REVIEW (2026-10-06)

- Avant `effective_date + 1 jour`, l'old SKU est calculé normalement (comme Excel) mais passe en REVIEW avec la raison « Conversion prévue le … vers … ». Le planner décide.

## D-010 — Date de livraison : obligatoire, affichage seulement (2026-10-06)

- La date de livraison est saisie à chaque run (aucune valeur par défaut) et doit être ≥ date du run.
- Elle n'entre **pas** dans la quantité (comme Excel). Elle sert à afficher le stock projeté le jour de la livraison (`projected_stock_at_delivery` = Expected − ventes/jour × jours jusqu'à la livraison), l'équivalent du « Closing stk » d'Excel, calculé ici avec la vraie date au lieu de S4 − 3.

## D-011 — Sélection des lignes par la règle « Filter out » (2026-10-06)

- Hors test de parité, les lignes sont choisies par la règle de CLAUDE.md (portfolio `Yes` → exclu ; hors portfolio sans stock ni ventes → exclu). Chaque exclusion a sa raison, visible dans l'onglet Résumé.
- Un new SKU est gardé si son old SKU a de l'activité. Les SKU d'allocations et de lancements sont toujours gardés.
- Sur B80 : 398 lignes retenues (Excel : 461). Écart attendu tant que Q-002 n'est pas résolue.

## D-012 — Arbitrage prévision / allocation / lancement / stock cible (2026-10-06)

- Une ligne n'a qu'**une** source de quantité (pas de double comptage) : allocation > lancement > stock cible > prévision. La quantité prévision reste visible (`forecast_qty`).
- Allocation : remplace la prévision ; due = total × % cumulé des vagues jusqu'à la vague en cours − déjà envoyé, arrondi à l'unité la plus proche, jamais négatif ; non arrondie au multiple mais signalée REVIEW si elle n'en est pas un.
- Lancement : jusqu'à la date de lancement incluse, commande = quantité initiale − Expected, arrondie au multiple ; ensuite, la prévision reprend.
- Stock cible : si Expected + commande < min → on remonte à la cible ; si > max → on plafonne au multiple inférieur.
- Ces règles sont des **hypothèses** (le brief n'est pas dans le dépôt) : voir Q-013 à Q-016.

## D-013 — Hors périmètre pour l'instant : Bible et couche IA (2026-10-06)

- Prompt 6 (parser la Bible) : CLAUDE.md dit de ne pas la parser pour l'instant. Non fait.
- Prompt 7 (API Claude) : soumis à l'accord IT et à la règle « aucune donnée réelle envoyée à un LLM ». Non fait. Le résumé des anomalies d'un run est produit de façon déterministe (`app/engines/anomalies.py`).

## D-014 — Ingestion : en-têtes cherchés, dates jamais devinées (2026-10-06)

- Les en-têtes sont cherchés dans les 25 premières lignes, avec plusieurs noms possibles par colonne. Si une colonne obligatoire manque, le fichier est refusé avec la liste des colonnes attendues.
- Dates : ISO accepté ; sinon un seul format doit marcher pour toute la colonne. Si « 03/04/2026 » peut être lu de deux façons, le fichier est refusé.
- Un SKU stocké comme nombre dans Excel est signalé (les zéros ou décimales peuvent être perdus).
- `tools/extract_masters.py` n'extrait pas la colonne de notes du Schedule (risque de données personnelles).

## D-015 — Journée multi-boutiques et Schedule (2026-10-06)

- L'onglet Journée propose les boutiques dont un **jour de commande** du Schedule tombe le jour du run, et la **date de livraison** correspondante (prochain jour de livraison strictement après le run).
- Les **cover days du Schedule ne sont jamais utilisés** : le planner saisit le cover de chaque boutique. Le calcul refuse de partir s'il en manque un.
- Une erreur sur une boutique (données absentes) n'arrête pas les autres : elle est affichée sur sa ligne.

## D-016 — Règles boutique structurées (2026-10-06)

- Les règles en texte libre de la Bible (« max 4 pallets », « never exceed… ») deviennent une table `boutique_rules` remplie et validée par un humain : `max_pallets`, `max_units` (commande), `max_qty_sku`, `min_qty_sku` (SKU).
- Une règle **ne modifie jamais** une quantité : règle SKU → ligne en REVIEW ; règle de commande → alerte sur la commande. Les règles sont recontrôlées après chaque override.

## D-017 — Catégorie obligatoire pour un override (2026-10-06)

- En plus de la raison, de l'auteur et de l'heure (règle 6), chaque override a une catégorie : promo / événement, lancement, rupture, surstock, stock DC, donnée erronée, autre. Elle sert à analyser les décisions des planners (onglet Historique & overrides).
- Les bases créées avant cette version sont mises à jour automatiquement (colonne ajoutée, valeur « other » pour les anciens overrides).

## D-018 — Confidentialité de l'outil local (2026-10-06)

- `.streamlit/config.toml` coupe la télémétrie de Streamlit (`gatherUsageStats = false`) et n'écoute que sur le poste (`localhost`), conformément à « aucune donnée envoyée à un service externe ».

## D-019 — Dépôt du jour au lieu d'une connexion Power BI (2026-10-07)

- Décision A1 du planner. L'écran « Dépôt du jour » reçoit les deux exports, détecte la date d'extraction (nom du fichier, puis propriétés du classeur ; sinon le planner la confirme) et affiche la fraîcheur (vert aujourd'hui, orange hier, rouge au-delà).
- Les fichiers sont copiés dans `data/depot/<date>/` (non versionné). L'ancien module qui simulait une source « Power BI » a été supprimé.
- Le bouton « Prêt » reste grisé tant qu'un contrôle qualité est bloquant.

## D-020 — Sélection des lignes : deux règles selon le mode (2026-10-07)

- **Parité Excel** : règle « Filter out » d'Excel, portfolio comparé en numérique puis en texte (A4). Sur B80 : les 451 lignes d'Excel + `2007.70` = 452 lignes. L'écart sur `2007.70` est documenté (Q-023) au lieu d'être forcé.
- **Standard** : tous les SKU avec une activité (A5) : portfolio, Expected/Available/Incoming non nul, ou mouvement dans la fenêtre. Sur B80 : 460 lignes.
- Remplace D-011. Le test de parité des quantités calcule toujours les SKU du golden (D-007, 416/416) ; la sélection est testée à part (`test_a4_b80_selection_matches_excel_451_lines`).

## D-021 — Exclusions éditables, plus aucun SKU codé en dur (2026-10-07)

- Décision A8. Remplace D-008. Une exclusion (SKU, boutiques ou toutes, raison, auteur, date) bloque la ligne dans les deux modes : `BLOCKED`, quantité 0, source « Exclusion », raison visible dans le « Pourquoi ».
- La comparaison des SKU est numérique puis texte, comme le portfolio.
- Conséquence parité : `7010.70` reste à 0 tant qu'il est dans le référentiel ; si on le retire, l'outil le calcule normalement (écart volontaire avec Excel).

## D-022 — Multiples : arrondi partout, doublons signalés (2026-10-07)

- Décision A3. Allocation, lancement, stock cible et override sont arrondis au multiple **supérieur**, avec une raison « Arrondi au multiple de M : x → y ». Une saisie du planner (override) est arrondie de la même façon.
- Décision A9. Un SKU avec deux multiples passe en REVIEW. En mode Standard, si le multiple de la famille (240 VER / 800 ORI) est l'un des deux, il l'emporte (ex. `7922.70` → 800). En Parité Excel, la première ligne est gardée (comportement Excel).

## D-023 — SKU dormants et historique de secours (2026-10-07)

- Décisions A6 et A7. Une ligne sans ventes ni Expected mais avec du stock DC passe en REVIEW « Retour en stock DC » si elle est au portfolio ou a des ventes dans l'historique long.
- Quantité suggérée (mode Standard) : cover × moyenne journalière des X dernières semaines **complètes** avec ventes (X = 4 par défaut), arrondie au multiple. En Parité Excel, la suggestion est affichée mais pas appliquée (Excel donne 0).
- Sans historique suffisant (ex. démo B80 : 7 jours de mouvements), la ligne reste en REVIEW avec « Historique de secours insuffisant » et quantité 0 : le planner décide.

## D-024 — Interface en 6 écrans (2026-10-07)

- Ordre de la barre latérale : Dépôt du jour, Commande boutique, Journée, Export, Référentiels, Historique. Une tâche par écran.
- Couleurs fixes : OK vert, REVIEW orange, BLOCKED rouge, modifié bleu. Nombres alignés à droite avec séparateur de milliers, couvertures à 1 décimale.
- Une modification de quantité exige une catégorie et une raison ; le nom du planner est saisi une fois par session.
- La version web de démonstration reprend les mêmes écrans ; ses quantités sont calculées d'avance par le moteur Python, la page n'en calcule aucune.

## D-025 — Commande en une seule grille, catégories par type produit (2026-10-07)

- Demande du planner : l'écran Commande boutique ressemble au calculateur Excel. Une seule table pleine largeur, en-tête figé, défilement interne ; plus de regroupement par famille ni de lignes repliées (remplace la partie « tableau groupé par famille » de D-024).
- Filtres sans recalcul : Catégorie (Tous / Cafés / Machines / Accessoires / Consommables & sacs / Autres), Statut (Tous / OK / REVIEW / BLOCKED / Modifié), « Qty > 0 seulement », recherche SKU ou description. Le filtre Statut suit ce qui est affiché : une ligne modifiée n'apparaît que sous « Modifié ».
- Tri au clic sur chaque en-tête ; tri par défaut : REVIEW et BLOCKED en haut, puis Qty proposée décroissante. Les nombres sont de vrais nombres dans la grille (tri correct), les cases vides affichent « — ».
- La catégorie vient du type produit via un référentiel modifiable (`masters/categories.csv`, modèle `templates/categories.xlsx`) ; valeurs par défaut données par le planner. Un type inconnu va dans « Autres » et l'écran Référentiels liste ces types. Un fichier invalide est signalé et le classement par défaut est gardé (affichage seulement, ne bloque pas le calcul).
- Limite de Streamlit : une grille modifiable ne sait pas réagir au clic sur une ligne. Une petite case 🔍 en première colonne ouvre le panneau « Pourquoi » à droite (la version web, elle, ouvre le panneau au clic sur la ligne).
- « Accepter toutes les lignes OK » marque les lignes OK comme validées (« ✓ » dans le Statut) ; une ligne REVIEW se valide depuis son panneau « Pourquoi ».

## D-026 — Dépôt des vrais exports Power BI (2026-10-07)

- Vérifié sur les deux exports réels du planner (non versionnés) : les colonnes de Stock Movements diffèrent des données de test (« Product Nr », « Stock Mvt Date », « Mvt Code », « Quantity (Sum) »…). Elles sont ajoutées aux noms acceptés ; les anciens restent valables.
- **Signe des quantités** : l'export ne contient que les sorties, en négatif (filtre « Quantity (Sum) ≤ 0 »). Les données de test et le calculateur les ont en positif. Règle : toutes négatives → comptées en ventes positives (information affichée) ; toutes positives → gardées ; signes mélangés → fichier refusé avec la raison (on ne peut pas savoir ce qui est une vente). Aucun écart de parité : la quantité vendue est la même.
- **Doublons d'un même SKU au même emplacement** (cas réel : «  473ECO/B » avec un espace et « 473ECO/B ») : si une seule ligne a du stock, elle est gardée avec un avertissement ; sinon le fichier est bloqué (Q-026).
- **Une seule zone de dépôt** : les deux exports s'appellent « data - … » ; l'outil les reconnaît par leurs colonnes et les range dans la bonne case. Un fichier non reconnu est refusé avec un message. En mode démo, le dépôt est désactivé.
- **Vitesse** : lecture Excel avec `python-calamine` (≈ 1 s au lieu de ≈ 14 s pour 94 000 lignes), cache local du fichier lu (`data/depot/…/_cache_*.pkl`, jamais versionné) et données du jour partagées entre les écrans tant que les fichiers ne changent pas.
- **Référentiels** : ils s'importent maintenant depuis l'écran (calculateur `.xlsm` → multiples, conversions, DC, portfolio, Schedule), sans ligne de commande. En attendant, un bouton permet de partir des référentiels de test (B80, 05-oct-2026).

## D-027 — Version web : « Mes fichiers » calculés dans le navigateur (2026-10-07)

- Demande du planner : déposer ses vrais exports dans la version web, sans installer l'outil.
- **Même moteur, pas de copie** : la page charge Pyodide (Python compilé pour le navigateur) et le code `app/` du dépôt ; les quantités viennent de `run_planning`, comme dans l'outil installé. Aucun moteur réécrit en JavaScript. Testé : mêmes quantités que l'outil installé (B80 et B5 sur les exports réels, tests `test_web_bridge.py`).
- **Confidentialité** : les fichiers sont lus et calculés dans le navigateur ; la page n'envoie rien (elle ne peut joindre que ses propres fichiers). Rien n'est gardé après la fermeture de la page.
- **Lecture Excel** dans la page par SheetJS (cdnjs). Il perd le format date de certaines cellules des exports Power BI : les dates restées en numéros de série Excel (ex. 46295) sont converties si toutes sont plausibles, avec un message (`DATE_SERIAL`).
- **Référentiels** : ceux des données de test (multiples, conversions, DC, Schedule du 05/10/2026, portfolio de B80). Le portfolio des autres boutiques n'est pas encore disponible dans la version web.
- **Publication** : Pyodide et ses bibliothèques sont vérifiés par empreinte (`tools/fetch_pyodide.py`) ; les archives (.zip, .whl), refusées par la plateforme, sont publiées en base64 et décodées par la page.

## D-028 — Modifications sans justification obligatoire (2026-10-08)

- Décision du planner : une quantité modifiée compte tout de suite, sans bloc « Modifications à justifier ». La règle 6 de CLAUDE.md est modifiée en conséquence ; remplace D-006 (partie raison) et D-017.
- Toujours gardés : la quantité d'avant, la quantité saisie (arrondie au multiple supérieur), l'auteur (nom du planner, sinon « Planner non renseigné ») et l'heure. La ligne passe en « Modifié » et la raison « Modifié par … : avant → après » reste visible.
- Catégorie et raison deviennent facultatives : on peut les ajouter dans le panneau « Pourquoi ». L'Historique classe les modifications sans catégorie sous « Sans catégorie ».
- Base locale : la contrainte « raison d'au moins 3 caractères » est retirée ; les bases existantes sont migrées automatiquement (table recréée, toutes les modifications passées gardées).

