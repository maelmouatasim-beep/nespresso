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

## D-028 — Modifications sans justification obligatoire (2026-10-08) — remplacée par D-038

- Décision du planner : une quantité modifiée compte tout de suite, sans bloc « Modifications à justifier ». La règle 6 de CLAUDE.md est modifiée en conséquence ; remplace D-006 (partie raison) et D-017.
- Toujours gardés : la quantité d'avant, la quantité saisie (arrondie au multiple supérieur), l'auteur (nom du planner, sinon « Planner non renseigné ») et l'heure. La ligne passe en « Modifié » et la raison « Modifié par … : avant → après » reste visible.
- Catégorie et raison deviennent facultatives : on peut les ajouter dans le panneau « Pourquoi ». L'Historique classe les modifications sans catégorie sous « Sans catégorie ».
- Base locale : la contrainte « raison d'au moins 3 caractères » est retirée ; les bases existantes sont migrées automatiquement (table recréée, toutes les modifications passées gardées).


## D-029 — Événements SKU : une seule source pour promos, lancements, arrêts et demandes (2026-10-11)

- Demande du planner : remplacer la liste gardée en tête ou dans la Bible par une page « Événements SKU ». Un événement est saisi une fois et flague ou bloque les SKU dans toutes les commandes tant qu'il est actif. La Bible n'est pas lue : un template Excel (`templates/evenements_sku.xlsx`, 4 exemples fictifs) permet de recopier la feuille du jour ; l'import liste les lignes incomplètes avec la raison, il ne les ignore pas.
- **Modèle** (`app/domain/events.py`) : portée Équipe / Personnel, 8 types, 5 effets (flag, quantité imposée, quantité ajoutée, bloquer, remplacer), SKU toujours en texte, boutiques (toutes, par DC ou liste), début / fin (fin vide permise pour Arrêt et Interdiction, et pour une demande boutique « jusqu'à la prochaine commande »), jours d'anticipation (7 par défaut pour un lancement), quantités par boutique et par SKU (« * » = toutes / tous ; la plus précise gagne), multiple facultatif par SKU, fichier source, propriétaire, planner, statut. Pas d'email dans les champs de personnes.
- **Actif pour une commande** quand : statut Actif, début − anticipation ≤ date de livraison ≤ fin, boutique ciblée, et (Équipe, ou Personnel de ce planner, ou repris par lui).
- **Ordre d'application** : prévision → arbitrage métier (allocations, lancements, stock cible) → événements → modifications du planner. Sans événement actif, rien ne change : parité 416/416 vérifiée par un test qui passe des événements inactifs.
- **Stockage** : tables `sku_events`, `sku_event_history` (qui, quand, quels champs), `planners` (prénoms seulement), `event_validations` dans la base locale.
- **Interface** : 7 écrans (complète D-024) — « 5. Événements SKU » s'ajoute avant Référentiels et Historique.

## D-030 — Événements : priorités et conflits, jamais de choix silencieux (2026-10-11)

1. Problème de conversion (contradiction avec SKU Conversion, chaîne A → B → C) → BLOCKED.
2. Bloquer l'envoi (Interdiction, Arrêt) → BLOCKED, quantité 0. Une interdiction gagne toujours : si un autre événement donnait une quantité, la raison « Le blocage prime sur … » est écrite.
3. Quantité imposée ou ajoutée → REVIEW, source « Événement », arrondie au multiple supérieur. Deux quantités imposées différentes, ou une imposée + une ajoutée → BLOCKED « Deux événements contradictoires », quantité 0, à trancher par une modification. Deux quantités imposées identiques : appliquée une fois. Plusieurs quantités ajoutées : additionnées (remplacé par D-034).
4. Flag → REVIEW, la quantité du moteur reste.
- Une **allocation officielle n'est jamais écrasée** (règle 5) : la ligne garde l'allocation et passe en REVIEW avec la quantité que proposait l'événement.
- Une ligne déjà **bloquée par le moteur** (exclusion, portfolio, ancien SKU) reste bloquée ; la demande de l'événement est écrite dans les raisons.
- **Quantité imposée** = quantité de la commande (pas de déduction de l'Expected, contrairement au référentiel Lancements, Q-029). **Quantité ajoutée** = quantité du moteur + quantité saisie arrondie au multiple (jamais moins que demandé).
- Quantité non saisie pour la boutique → REVIEW « quantité non définie », quantité du moteur gardée.
- **Multiple** : celui de la Multiple list ; si elle n'en a pas (ou un multiple inféré / à 1 par défaut), le multiple saisi dans l'événement est utilisé et signalé. S'il diffère de la Multiple list, la Multiple list gagne et l'écart est signalé.
- **Conversion** par événement : ajoutée au moteur de conversion existant avec une date d'effet = jour du calcul − 1 (l'ancien SKU est bloqué tout de suite, le nouveau reprend ventes et Expected).
- **SKU absent de la Stock Situation** (lancement, demande) : toujours ajouté à la commande, avec la quantité saisie, ou REVIEW sans quantité si rien n'est saisi (ne jamais inventer, ne jamais oublier). Vrai aussi en mode Parité Excel (démo B80 : 453 lignes avec le lancement fictif ADV-2026-CA).

## D-031 — Garde-fou avant export et validations tracées (2026-10-11)

- Le fichier LT (et le zip de la Journée) ne se télécharge que si trois points sont verts : (1) chaque ligne BLOCKED par un événement est traitée — blocage confirmé, ou modification **avec une raison** (exception voulue à D-028 pour ces lignes seulement) ; (2) chaque SKU flagué par un événement est marqué « Vu » ou modifié ; (3) les lancements, urgences et demandes boutique à inclure sont cochés.
- Boutons « Confirmer les blocages » et « Marquer comme vus » pour toute la commande (ou la journée) : chaque clic est tracé (qui, quand, quels SKU) dans `event_validations` (Q-030).
- Après l'export, chaque demande boutique appliquée propose de se terminer ; « Terminer » est tracé dans l'historique de l'événement (« terminé (commande exportée) »).

## D-032 — Identité du planner et événements personnels (2026-10-11)

- Le planner choisit son prénom dans une liste éditable (barre latérale « Ajouter un planner », Référentiels → Planners). Prénom seulement, jamais d'email. Il sert à « Mes événements », aux événements personnels et à signer modifications et validations.
- Un événement personnel est visible des autres en lecture seule, avec le nom du planner. Commande boutique : un bandeau signale les événements personnels d'un collègue actifs pour la boutique et non appliqués. « Reprendre » l'applique à la session du collègue (tracé dans l'historique de l'événement ; non gardé après la session, Q-031).

## D-033 — Version web : événements fictifs en lecture seule (2026-10-11)

- La démo web contient les 4 événements fictifs de B80 (promotion, lancement absent de la Stock Situation, arrêt, demande boutique personnelle de Camille). La page « Événements SKU » est en lecture seule (filtres, détail, « Reprendre », terminer une demande) : création, modification, duplication et import Excel sont dans l'outil installé.
- Démo : la commande B80 est calculée d'avance avec les événements évalués pour la livraison du 08/10/2026 ; « Mes fichiers » envoie les événements au moteur Python du navigateur, qui choisit lui-même ceux actifs à la date saisie (mêmes quantités que l'outil installé, test `test_web_bridge.py`).

## D-034 — Quantités ajoutées additionnées (Q-027) (2026-10-11)

- Décision du planner : deux (ou plus) événements qui **ajoutent** une quantité au même SKU (ex. promo + demande boutique) s'additionnent. La ligne passe en REVIEW (plus de BLOCKED dans ce cas) et chaque événement est cité dans « Pourquoi », suivi du total (« 720 calculé + 240 + 240 = 1200 »).
- Chaque quantité ajoutée est arrondie au multiple supérieur séparément, puis les quantités arrondies sont additionnées : le total est la somme de ce que chaque événement aurait ajouté seul. Même quantité ajoutée par deux événements : comptée deux fois. Un SKU listé deux fois dans le même événement compte une fois.
- Les autres conflits restent BLOCKED « Deux événements contradictoires » : deux quantités imposées différentes, une imposée + une ajoutée, et blocage contre quantité (le blocage gagne).
- Version de règle : `events-1.1.0`.

## D-035 — Garde-fou : « Marquer comme vus » après le filtre Événements ; « Reprendre » limité à la session (Q-030, Q-031) (2026-10-11)

- Décision du planner : la validation globale en un clic est gardée (« Confirmer les blocages », « Marquer comme vus »), mais **« Marquer comme vus » n'est actif que si le planner a ouvert au moins une fois le filtre « Événements »** (bandeau « Événements actifs ») de la commande en cours. Une commande = boutique + date de livraison ; l'ouverture est retenue pour la session.
- Tant que ce n'est pas fait, le bouton est grisé et une phrase dit quoi faire. Dans la Journée, la phrase nomme les boutiques à « Ouvrir » d'abord ; le bouton s'active quand toutes les boutiques avec des flags non vus ont été regardées.
- « Confirmer les blocages » ne change pas. Valider une ligne à la fois (« Marquer comme vérifiée » dans « Pourquoi ») reste toujours possible.
- Q-031 fermée : « Reprendre » un événement personnel reste limité à la session (D-032 inchangée).
- Même règle dans la démo web.

## D-036 — Catégorie de modification obligatoire, en un clic dans la ligne (révise D-028) (2026-10-11) — annulée par D-038

- Décision du planner : toute modification de quantité exige une **catégorie**, choisie en un clic dans la ligne du tableau (colonne « Catégorie modif. »), parmi : Promo, Lancement, Rupture, Surstock, Stock DC, Donnée erronée, Demande boutique, Autre. « Demande boutique » est nouvelle. La raison libre reste facultative (panneau « Pourquoi »). Règle 6 de CLAUDE.md mise à jour.
- Le modèle `Override` refuse une modification sans catégorie. Auteur et heure restent toujours gardés.
- Une quantité tapée sans catégorie n'est **pas appliquée** : la ligne affiche la quantité saisie avec le statut « À catégoriser » et un bandeau compte les lignes en attente. L'export du fichier LT est bloqué tant qu'il en reste (rien de silencieux). La catégorie peut aussi être choisie avant la quantité.
- Revenir à la quantité proposée retire la modification, sans catégorie à donner.
- Lignes BLOCKED par un événement : une modification doit toujours avoir **une raison** pour lever le blocage (D-031 inchangée).
- Base locale : la colonne `category` reste nullable pour garder les modifications passées enregistrées sans catégorie ; l'Historique les classe sous « Sans catégorie ». Pas de migration.
- Libellés courts pour tenir dans la cellule (« Promo » au lieu de « Promo / événement », etc.) ; les valeurs stockées ne changent pas.
- Même règle dans la démo web.

## D-037 — Rien en lecture seule : référentiels et événements modifiables partout (révise D-033) (2026-10-12)

- Décision du planner : « je ne veux rien en lecture seule, je veux avoir la main sur tout l'outil ».
- Outil installé : Multiples, Conversions, Portfolio, Boutiques et DC, Schedule, Allocations, Lancements, Stocks cibles et Règles boutique deviennent des grilles modifiables (cellules, lignes ajoutées ou retirées, bouton « Enregistrer »). Exclusions et Catégories l'étaient déjà. Avant d'écrire, la table est relue par le **même lecteur qu'à l'import** (`app/ingestion/referential_edit.py`) : une erreur bloque l'enregistrement avec sa raison, le fichier n'est pas touché. Les avertissements (SKU en double, jour illisible au Schedule) sont affichés sans bloquer. La mise à jour est tracée (auteur, heure, « modifié dans l'outil »). Une table métier enregistrée depuis la grille est écrite en `.csv` (elle remplace l'ancien `.xlsx`).
- Démo web : mêmes grilles (recherche, pages de 50 lignes) et événements SKU créables, modifiables, duplicables, terminables et annulables. Chaque table et chaque événement est vérifié par le moteur Python de la page (mêmes règles), puis gardé dans ce navigateur. Dès qu'une modification existe, la commande B80 est **recalculée en direct** à partir des exports de test, au lieu du résultat calculé d'avance. « Revenir aux données de test » / « Revenir aux événements de démo » efface les modifications.
- Lecteurs CSV des tables maîtres réécrits sans pandas (module `csv`) pour tourner aussi dans la page ; parité 416/416 inchangée.
- Exemple : dans la démo, mettre le multiple de `7005.70` à 1000 sur la première ligne laisse 240 : le SKU a alors deux multiples (240 et 1000) et la règle A9 garde le multiple de la famille, ligne en REVIEW « Deux multiples ». Pour changer vraiment un multiple en double, il faut modifier toutes ses lignes.
- Événements Personnels d'un collègue : réglage `PERSONAL_EDITABLE_BY_OTHERS` (`app/domain/events.py`), en attente de la réponse du planner.

## D-038 — Modification de quantité immédiate, contexte déduit par l'outil (remplace D-028 et D-036) (2026-10-12)

- Décision du planner : la colonne « Catégorie modif. » est supprimée de la table de commande (outil installé et démo web). Plus de statut « À catégoriser », plus de bandeau jaune, plus de blocage de l'export. Règle 6 de CLAUDE.md mise à jour.
- Une Qty finale tapée compte **tout de suite** : totaux, palettes, export. La ligne passe en « Modifié » (bleu). Revenir à la quantité proposée retire la modification.
- Enregistré automatiquement, sans rien demander : ancienne qty, nouvelle qty, planner, heure, et un **contexte déduit** par une fonction pure (`deduce_context`, `app/engines/overrides.py`), lu sur la ligne avant modification. Priorité : événement actif sur le SKU > stock DC insuffisant pour la qty saisie (arrondie) > SKU dormant (ni Expected ni ventes, ou règle A6) > « Non précisé ». Le détail cite tout ce qui a été trouvé (ex. « Événement actif : « Promo AOS » · Stock DC insuffisant (0 < 240) »).
- Le contexte n'apparaît **que** dans le panneau « Pourquoi » et dans l'Historique, jamais dans la table (il n'entre pas dans la colonne « Raisons »). L'export Excel détaillé (onglet Overrides) le reprend dans « Contexte déduit ».
- Arrondi au multiple supérieur gardé (A3) : si la qty tapée n'est pas un multiple, un petit avertissement s'affiche une fois sous les filtres et la raison « ⚠ Saisie X arrondie au multiple de M : Y » reste sur la ligne.
- Historique : modifications comptées par contexte déduit, SKU les plus modifiés, détail avec le contexte. Les modifications enregistrées avant cette décision gardent leur ancienne catégorie (« Catégorie : Promo », etc.).
- Base locale : deux colonnes ajoutées à la table `overrides` (`context`, `context_detail`), sans toucher aux lignes existantes. Le champ `category` reste lisible mais n'est plus demandé.
- Inchangé : la raison libre reste facultative dans « Pourquoi » ; une ligne BLOCKED par un événement demande toujours une confirmation ou une modification avec raison avant l'export LT (garde-fou D-031, distinct de cette règle).
- Même règle dans la démo web.

## D-039 — Modifier une quantité et confirmer une ligne sont deux actions distinctes (2026-10-12)

- Problème signalé par le planner : dans le filtre REVIEW, changer une Qty finale faisait passer la ligne en « Modifié », elle sortait du filtre et disparaissait, comme si elle avait été confirmée.
- Modifier une Qty finale **ne change jamais le statut** : une ligne REVIEW modifiée reste REVIEW, au même endroit. Un marqueur « modifié » s'affiche à côté du statut et la cellule Qty finale passe en bleu très léger. « Modifié » n'est plus un statut. Le moteur d'overrides ne fait plus passer une ligne BLOCKED en REVIEW quand une quantité y est saisie ; il ajoute seulement la raison « Quantité saisie sur une ligne bloquée : à vérifier ». Le statut affiché est celui du calcul (avant modification), même si une règle boutique réagit à la qty saisie (sa raison reste visible).
- **Confirmer** est explicite : case « Confirmé » en début de ligne (aussi « Confirmer la ligne » dans « Pourquoi »). La ligne passe en « Confirmé » (vert) ; décocher la remet à traiter. « Accepter toutes les lignes OK » confirme toutes les lignes OK, modifiées comprises ; « Confirmer les lignes affichées » confirme ce qui est à l'écran après filtrage. Confirmer une ligne bloquée par un événement confirme aussi ce blocage (tracé, D-031) ; « Marquer comme vus » du garde-fou confirme les SKU flagués.
- Filtres de statut : Tous / À traiter (OK + REVIEW non confirmés) / REVIEW / BLOCKED (non confirmés) / Confirmé / Modifié (Qty finale ≠ Qty proposée, quel que soit le statut). Tuiles : OK, REVIEW, BLOCKED non confirmés, et Confirmé.
- Export : le fichier LT reste désactivé tant qu'une ligne REVIEW ou BLOCKED n'est pas confirmée (le nombre est affiché, avec un retour à la commande). Le garde-fou en 3 points des événements ne change pas. L'écran Journée (zip de plusieurs boutiques) garde son garde-fou actuel : il n'a pas de confirmation ligne par ligne.
- Pas de re-tri après une saisie : la grille garde la même identité tant que les lignes affichées ne changent pas, donc la ligne reste en place et le tri choisi au clic est gardé. Le tri par défaut se calcule sur le statut et la qty du calcul, jamais sur la qty saisie. Exception : une saisie hors multiple recrée la grille pour afficher la qty arrondie.
- Même règle dans la démo web.

## D-040 — Une seule page « Commandes » (remplace « Commande boutique » et « Journée ») (2026-10-12)

- Décision du planner : les pages « Commande boutique » et « Journée » fusionnent en une page « 2. Commandes ». La page « Journée » disparaît, ainsi que toute présélection depuis le Schedule (plus de boutiques pré-cochées ni de date suggérée). Le Schedule reste un référentiel, il ne pilote plus la commande.
- En haut, un champ « Ajouter une boutique » (recherche par code ou nom), vide au départ. Chaque boutique ajoutée devient un onglet avec une croix pour la retirer. Une boutique = une commande simple, plusieurs = une journée.
- Une ligne de paramètres par boutique : livraison, cover non-café, cover café (facultatif), historique, mode (et options avancées). Les paramètres de la première boutique sont proposés aux suivantes, modifiables. « Calculer » par boutique et « Tout calculer ». Des paramètres modifiés après un calcul remettent la boutique « à calculer » jusqu'au recalcul.
- L'onglet montre : code, statut (à calculer / calculé / confirmé), REVIEW et BLOCKED restants (non confirmés). Un clic affiche la table de commande existante (filtres, Qty finale, Confirmé, « Pourquoi », événements). L'état de chaque boutique (résultat, modifications, confirmations, filtres) est gardé quand on change d'onglet.
- Barre récapitulative sous les onglets : livraison, unités, palettes, lignes à traiter, REVIEW, BLOCKED par boutique et au total.
- Export depuis la page : « Télécharger le LT » de la boutique affichée et « Télécharger tous les LT (zip) ». Chacun passe par le garde-fou de D-039 (REVIEW et BLOCKED confirmés) et les 3 points des événements (D-031). Le zip attend que chaque boutique soit prête et cite celles qui bloquent encore avec la raison (hypothèse retenue, le message du planner s'arrêtant à « chacun avec le garde-fou »). Chaque boutique du zip est enregistrée dans l'historique avec ses modifications.
- La page « 3. Export » garde le récapitulatif boutique, l'export Excel détaillé et « Enregistrer sans exporter » pour la boutique affichée.
- Même fonctionnement dans la démo web. Parité 416/416 inchangée (le moteur n'a pas bougé).

## D-041 — Référentiels en 3 onglets : Produits, Boutiques, Planners (2026-10-12)

- Décision du planner : la page « Référentiels » n'a plus que 3 onglets.
- **Produits** : une ligne par SKU, avec SKU, description, type, catégorie, multiple, unités par palette, ancien SKU remplacé et date de conversion, exclu (oui = toutes les boutiques, ou codes séparés par ;) et raison. Le tableau réunit la Multiple list, la table des catégories, les conversions et les exclusions (`app/ingestion/product_table.py`, sans pandas, le même code dans la démo web). Filtres SKU / description, catégorie et « Anomalies » (puce orange : doublon, espace parasite, multiple 0 ou 1 sur un café, multiple inféré ou manquant, SKU absent de la Multiple list). Import / export Excel : l'import met à jour ou ajoute les SKU présents, il ne retire rien.
- À l'enregistrement, seuls les SKU modifiés sont réécrits. Les autres lignes de la Multiple list restent identiques, doublons compris, donc la parité 416/416 est inchangée. Un multiple, un type, une description ou des unités par palette modifiés s'appliquent à **toutes les lignes du SKU**, ce qui supprime le doublon de multiples (cas 7005.70). La catégorie dépend du type produit : la changer sur une ligne reclasse tout le type. Une ligne retirée enlève le SKU de la Multiple list, ses conversions et son exclusion. Les conversions « XYZ123 » (pas de remplaçant) sont sur la ligne `XYZ123`.
- **Boutiques** : une ligne par boutique, avec code, nom, DC (une colonne, plus d'onglet DC), carrier et planner habituel. Le nom modifié et le planner habituel sont gardés dans `master_boutiques.csv` ; le nom y prime sur celui du Schedule. Le carrier est écrit sur les créneaux du Schedule de la boutique. Au clic sur une boutique : ses règles (palettes max, unités max, min / max par SKU) et ses créneaux de livraison. Le portfolio de la boutique est dans un sous-onglet.
- **Planners** : la liste des prénoms.
- Chaque onglet garde son propriétaire, sa date de mise à jour et l'**historique des modifications** (`_historique.jsonl` dans le dossier des référentiels ; dans la démo, ce navigateur).
- Allocations, lancements et stocks cibles : provisoirement dans un bloc replié sous les onglets. Ils passent dans les Événements SKU (types Promotion, Lancement et un nouveau type « Stock cible ») dans un second temps, sur une branche, parce que ce passage change le moteur et déplace les données existantes.

## D-042 — Allocations, lancements et stocks cibles deviennent des événements SKU (2026-10-12)

- Décision du planner : les onglets Allocations, Lancements et Stocks cibles quittent Référentiels. Tout passe par la page « Événements SKU ».
- **Promotion**, nouvel effet « Allocation officielle (vagues) » : par boutique et SKU, total, déjà envoyé, vagues en % (ex. 75/25, vide = une vague) et vague en cours. Fin vide permise (une allocation se termine à la main). Jamais écrasée par la prévision ni par une quantité d'événement (règle 5).
- **Lancement**, nouvel effet « Quantité initiale − Expected » : début = date de lancement ; jusqu'à cette date, quantité initiale − Expected arrondie au multiple ; ensuite la prévision reprend (comme l'ancien fichier).
- Nouveau type **« Stock cible »** (effet « Min / cible / max », par boutique) : la demande disait « quantité = cible − expected, arrondie au multiple ». C'est ce qui s'applique dès que le stock après commande passe sous le min ; au-dessus du max, la quantité est plafonnée ; entre les deux, la prévision reste. C'est exactement l'ancienne règle des stocks cibles, gardée pour ne changer aucune quantité. Pour toujours remonter à la cible, mettre min = cible. (Q-032)
- Calcul : `business_from_events` transforme les événements actifs de la boutique en entrées de l'arbitrage métier existant (`business.py`, inchangé). Vérifié : sur la démo B80, les 4 combinaisons (Standard / Parité × Équipe / avec la demande personnelle) donnent les mêmes quantités et statuts qu'avec les anciens exemples, sur toutes les lignes. Parité 416/416 inchangée.
- Deux valeurs différentes pour un même SKU (deux événements, ou un événement et un ancien fichier) → `BLOCKED` « deux événements contradictoires » ; deux valeurs identiques comptent une fois. Une interdiction ou un arrêt gagne toujours. Une quantité non définie pour la boutique → `REVIEW`, quantité du moteur gardée.
- Une ligne venant d'un de ces événements suit le garde-fou des événements (« Vu » avant l'export ; un lancement est à inclure).
- Template : un seul classeur, `templates/evenements_sku.xlsx` (onglet Quantités : Min, Max, Déjà envoyé, Vagues (%), Vague en cours). `allocations.xlsx`, `launches.xlsx` et `target_stock.xlsx` sont supprimés.
- Données déjà en place : les anciens fichiers comptent encore dans les commandes (rien de perdu en silence), avec un bandeau sur la page Événements SKU. Le bouton « Convertir en événements » crée un événement Équipe par boutique et par fichier (et par date de lancement), avec les mêmes valeurs, puis range les fichiers dans `masters/_archives/` (jamais supprimés) et l'écrit dans l'historique.
- Démo web : la case « Exemples d'allocation, lancement, stock cible » disparaît ; ces exemples sont trois événements de démo. La démo de l'outil installé garde ses 4 événements de départ.
- Règle de travail (même jour) : toujours sur `main`, jamais de branche ni de PR, même pour un changement risqué ; le risque est dit en une phrase dans le résumé.

## D-043 — Colonnes figées en bloc et une seule liste « lignes qui ne partiront pas » (2026-10-12)

- Table de commande : les premières colonnes (Confirmé, Statut, SKU ; plus la petite case Détail dans l'outil installé) sont figées ensemble, sans espace, et le reste défile à droite. Dans la démo web, la colonne Statut était figée à 104 px alors que Confirmé fait 76 px : trou blanc et SKU à moitié caché. Vérifié à 1280 px (13 pouces) et 1920 px : à l'ouverture aucune colonne n'est masquée ; en défilant, les colonnes passent sous le bloc figé et reviennent en défilant à gauche.
- Zone export : le bloc « Vérification avant export (événements SKU) » en 3 points et le message rouge « X lignes à confirmer » sont remplacés par une seule liste : titre « X lignes ne partiront pas si tu ne les confirmes pas », puis les lignes (SKU, description, statut, qty proposée, raison en quelques mots), présentées comme la table de commande, avec une case Confirmé par ligne et « Tout confirmer ». Sont dans la liste : REVIEW et BLOCKED non confirmés, et les lignes flaguées par un événement (lancement, demande boutique, promo…) non confirmées, avec le titre de l'événement en raison quand le calcul n'en donne pas. Liste vide → « Tout est confirmé » et les téléchargements sont actifs. « Télécharger le LT » reste désactivé tant que la liste n'est pas vide, sans autre message.
- Conséquences : la case « J'ai vérifié : ces lancements et demandes sont inclus » et « Marquer comme vus » disparaissent ; confirmer la ligne les remplace (tracé « seen » / « confirm_block » comme avant). Q-030 (filtre « Événements » à ouvrir d'abord) ne s'applique plus. Le moteur `export_check` reste tel quel (tests unitaires), l'interface ne l'utilise plus.
- Même règle dans la démo web.

## D-044 — La colonne Expected suit la Qty finale (2026-10-12)

- Demande du planner : quand il change une quantité, la colonne Expected doit monter ou baisser avec ce qui partira vraiment. Pas de colonne en plus : la colonne « Expected » de la table affiche désormais Expected du jour (Stock Situation) + Qty finale, et se recalcule à chaque saisie (modification immédiate, D-038).
- Le calcul ne change pas : la formule utilise toujours l'Expected du jour, cité tel quel dans le panneau « Pourquoi » (« Stock attendu en boutique (Expected) »), qui affiche aussi « Expected après commande : X (Expected du jour + Qty finale) ». « Couv. après » reste cohérente avec la même somme. Parité 416/416 inchangée.
- Même règle dans la démo web (le survol de la cellule montre la somme).

## D-045 — SKU d'un événement : sélection multiple avec recherche (2026-10-12)

- Signalé par le planner : en ajoutant un SKU à un événement, la liste ne se mettait pas à jour, et le choix de plusieurs SKU pour une même promotion n'était pas visible. Cause : dans l'outil installé, la zone de texte n'était relue qu'en quittant le champ (ou Ctrl+Entrée) ; dans la démo web, aucun aperçu des SKU n'était affiché.
- Le formulaire montre maintenant une **sélection multiple avec recherche** (SKU ou description) : chaque SKU choisi apparaît tout de suite dans la liste, avec sa description et son état (reconnu / inconnu). On peut aussi **coller une liste** (colonne Excel) : les SKU s'ajoutent à la sélection, doublons retirés (comparaison comme Excel, 7005.7 = 7005.70) et comptés. Un SKU inconnu reste possible, signalé.
- Un événement porte autant de SKU que nécessaire et s'applique à chacun (déjà vrai dans le moteur et la base : aucun changement de moteur ni de base). Les conversions gardent leur grille Ancien / Nouveau SKU.
- Même fonctionnement dans la démo web (puces avec ×, recherche dans les SKU de la commande B80, collage). Parité 416/416 inchangée.

## D-046 — Une seule colonne « Couverture », colonnes finales de la table (2026-10-12)

- Demande du planner : remplacer « Couv. act. » et « Couv. après » par une seule colonne « Couverture », toujours calculée sur Expected + Qty finale (Qty finale 0 = couverture actuelle ; dès qu'une quantité est saisie, le chiffre monte tout seul). Couleur discrète : rouge sous la cible saisie (cover café si café et cover café renseigné, sinon cover), vert au-dessus (égal = vert). Sans ventes : pas de couverture, pas de couleur.
- Colonnes finales, dans cet ordre : Confirmé · Statut · SKU · Description · Expected · Available · Ventes · Qty proposée · Qty finale · Expected + commande · Couverture · Multiple · Stock DC · Raisons. Précise D-044 : « Expected » redevient l'Expected du jour et « Expected + commande » (Expected du jour + Qty finale) suit la saisie. « Available » (stock disponible du jour, Stock Situation) est ajouté au `Pourquoi` du moteur (`inputs.available_total`, affichage seulement). Les colonnes Catégorie, Événement et Source quittent la table : le filtre Catégorie fonctionne toujours, l'événement et la source restent dans le panneau « Pourquoi » (ligne « Événement(s) »). L'outil installé garde la petite case « Détail », seul moyen d'ouvrir le « Pourquoi » dans Streamlit (dans la démo : clic sur la ligne).
- Info-bulle « actuelle 6,5 j → après commande 9,6 j (cible 9,0 j) » sur la cellule dans la démo web. Streamlit n'a pas d'info-bulle par cellule : le même détail est dans le panneau « Pourquoi » (« Couverture : actuelle … → après commande … · cible … ») et l'aide de la colonne l'explique.
- Les colonnes « Couv. actuelle (j) » / « Couv. après (j) » de l'export Excel détaillé ne changent pas. Parité 416/416 inchangée.

## D-047 — Table de commande allégée : Raisons en texte court, Available, 13 pouces (2026-10-12)

- Colonnes Source, Événement et Catégorie retirées de la table. Ce qu'elles disaient passe dans « Raisons », en texte court, une seule colonne de pointage tout à droite : la source (« Prévision », « Allocation », « Retour DC »…), les événements (« Promo Festive AOS », « Lancement Advent », « Demande B19 », « Arrêt d'envoi »), puis chaque raison coupée au premier « : », « ( », « , » ou « → » (« Stock DC insuffisant », « Multiple inféré depuis la famille »). Sans doublon : « Source : … » et la raison qui répète le titre de l'événement sont omises ; la source « Événement » / « Modifié » est portée par le tag de l'événement / « Modifié par … ». Le détail complet reste dans le « Pourquoi » (et dans l'info-bulle de la cellule dans la démo). Les filtres Catégorie et Événements fonctionnent comme avant.
- « Available » (stock physiquement en boutique) juste après « Expected », comme dans le calculateur Excel ; l'écart Expected − Available = en route, pas encore reçu : info-bulle « + 4 080 en route » sur la cellule dans la démo, ligne « Available (en boutique) : X · + Y en route » dans le « Pourquoi » de l'outil installé (Streamlit n'a pas d'info-bulle par cellule). « Expected + commande » juste après la Qty finale, comme « Expected + replen » d'Excel (D-046).
- Le planner a envoyé dans la même minute « Simplifie encore » (une seule colonne Couverture, D-046) et cette demande qui liste encore « Couv. act. » et « Couv. après ». Choix appliqué : une seule colonne « Couverture » (demande de simplification), à confirmer par le planner ; revenir à deux colonnes est une ligne à changer dans `ORDER_COLUMNS` et `COLS`.
- En-têtes abrégés (Conf., Statut, SKU, Description, Exp., Avail., Ventes, Prop., Finale, Exp.+cmd, Couv., Mult., DC, Raisons), nom complet dans l'aide de l'en-tête. Démo web : largeurs serrées (somme 1 000 px, police 13 px), table à 100 % de la zone : vérifié à 1 280 px (13 pouces) sans colonne cachée ni défilement horizontal, et à 1 920 px ; le marqueur « modifié » devient un point bleu dans la colonne Statut (texte complet dans le « Pourquoi »). Outil installé : mêmes largeurs réduites ; sur 13 pouces, replier la barre latérale Streamlit pour tout voir sans défiler.

## D-048 — Double confirmation quand le stock DC est à 0 ou insuffisant (2026-10-12)

- Règle du planner : confirmer une ligne dont la Qty finale > 0 alors que le stock DC du SKU est à 0, absent de l'export DC, ou inférieur à la quantité ne se fait pas en un clic. Premier clic sur « Confirmé » : la case reste décochée, la ligne passe en orange avec « Stock DC : 0 — confirmer quand même ? » (ou « Stock DC : 120 < 240 ») ; second clic : confirmée, validation tracée `confirm_dc0` « Confirmé malgré DC à 0 » (Historique). À l'export (LT ou zip), les lignes concernées sont tracées `export_dc0` avec la même raison.
- Les lots (« Accepter toutes les lignes OK », « Confirmer les lignes affichées », « Tout confirmer » de la liste avant export) excluent toujours ces lignes : elles restent dans la liste « X lignes ne partiront pas… », en orange, avec un message qui les nomme, à confirmer une par une (case de la ligne ou « Confirmer la ligne » du Pourquoi). Le LT reste désactivé tant qu'elles ne sont pas confirmées.
- Si la Qty finale redescend à 0 ou sous le stock DC, l'attente et la trace tombent d'elles-mêmes (vérifié sur la quantité du moment). État par boutique (`dc_warn`, `dc_forced`), outil installé et démo web.

## D-049 — SKU inactifs masqués à l'import (2026-10-12)

- Règle du planner : un SKU avec Expected = 0, Available = 0, Incoming = 0, aucune vente sur la période (fenêtre du calcul et historique de secours) et stock DC = 0 ou absent dans le DC de la boutique est un ancien SKU. Moteur pur `app/engines/inactive.py` (`is_inactive`) ; le drapeau `inactive` est dans le « Pourquoi » de chaque ligne (`compute_line`). La ligne reste calculée : parité Excel 416/416 inchangée.
- Exceptions, toujours affichées : SKU avec un événement actif (lancement, promo, demande boutique…), nouveau SKU d'une conversion active, ou quantité saisie par le planner.
- Table de commande : les SKU inactifs n'apparaissent pas et ne comptent pas dans les tuiles, les filtres, la liste avant export ni les onglets. Case discrète « Afficher les SKU inactifs (N) » sous la table pour les voir. Rapport qualité du Dépôt du jour : tuile « SKU inactifs » (candidats d'après les deux exports, toutes boutiques ; les exceptions ne sont connues qu'au calcul, le nombre par boutique dans la commande est donc plus petit).
- Outil installé et démo web ; CLAUDE.md mis à jour.

## D-050 — Écran Commandes : une seule table à onglets de statut, export juste dessous (2026-10-12)

- Décision du planner : pas de barre d'étapes ni de barre d'actions fixe (« on est des professionnels, il nous faut une calculatrice complète avec des infos bien claires »). Une maquette avec ces éléments a été montrée et refusée ; l'écran garde les paramètres en haut, une seule table, l'export juste dessous. Aucun changement de calcul ni de règle : D-039 (modifier ≠ confirmer), D-043 (lignes à confirmer), D-048 (double confirmation DC à 0) restent telles quelles ; parité 416/416 inchangée.
- Onglets de statut au-dessus de la table, par priorité : « À vérifier (N) » ouvert par défaut = lignes non confirmées qui ne partiront pas sans confirmation (BLOCKED, REVIEW, OK flaguée par un événement), triées BLOCKED → REVIEW → événement puis Qty proposée décroissante, raison visible dans « Raisons » ; « OK (N) » = OK non confirmées sans événement, avec le seul bouton « Accepter toutes les lignes OK » (n'accepte jamais une OK flaguée) ; « Confirmées (N) » ; « Toutes (N) » avec le tri par défaut d'avant. « Confirmer les lignes affichées (N) » sur À vérifier et Toutes. Les filtres « À traiter », « REVIEW », « BLOCKED », « Confirmé » et « Modifié » disparaissent ; « Modifié » devient la case « Modifiées seulement », à côté de « Qty > 0 seulement » ; catégorie et recherche restent sous les onglets, sans recalcul.
- La seconde table « N ligne(s) ne partiront pas si tu ne les confirmes pas » est retirée : sous la table, une ligne d'état « N ligne(s) à vérifier avant l'export » avec « Voir » (ouvre l'onglet À vérifier), ou « Tout est confirmé », puis les boutons LT / zip / Récap. Les garde-fous sont inchangés : le LT attend que « À vérifier » soit vide (lignes DC à 0 incluses, toujours exclues des lots), le zip attend que chaque boutique soit prête.
- Tuiles réduites à trois : Unités, Palettes estimées, À vérifier (détail BLOCKED · REVIEW · événement). Onglets de boutique avec leur état : « B80 · 93 à vérifier », « B80 · prête », « B80 · à calculer ». Récapitulatif de la journée replié par défaut (titre : boutiques, unités, palettes, à vérifier ; colonnes Boutique, État, Livraison, Unités, Palettes, À vérifier).
- Outil installé et démo web ; tests des garde-fous d'export réécrits sur l'onglet « À vérifier » ; CLAUDE.md mis à jour.

## D-051 — Plafonnement de la Qty proposée au stock DC en mode Safe (2026-10-13, remplace D-048)

- Règle du planner (12-oct-2026, soir) : la double confirmation « DC à 0 » (D-048) est retirée. À la place, en mode **Safe** (le mode par défaut, ex-« Standard » : même moteur `ForecastMode.STANDARD`, seul le libellé change), la Qty proposée est **plafonnée au stock DC disponible, arrondie au multiple inférieur** : DC = 0 → 0 ; DC = 500 avec un multiple de 240 → 480. Moteur pur `app/engines/dc_cap.py` (`cap_to_dc`, `floor_to_multiple`), appliqué dans `run_planning` après l'arbitrage métier et les événements, donc aussi aux quantités imposées (lancement, allocation, stock cible, événement). C'est un changement de moteur : nouvelle version de règle `dc_cap-1.0.0` dans `rule_versions` et dans le « Pourquoi » de chaque ligne (`explanation["dc_cap"]` = besoin, DC, qty, multiple).
- La ligne garde son statut (OK reste OK) : elle se confirme d'un clic ou avec « Accepter toutes les lignes OK ». Raison « Plafonné au stock DC (besoin 1 200, DC 500 → 480) » dans la colonne Raisons, cellule Stock DC orange, étape dédiée dans le « Pourquoi », filtre « Plafonnés DC (N) » à côté de « Modifiées seulement ». Le REVIEW « Stock DC insuffisant » du moteur de prévision n'est plus levé en mode Safe (il est remplacé par le plafond) ; il reste levé en parité Excel.
- Une Qty finale saisie **au-dessus du stock DC** est acceptée, mais la ligne passe en REVIEW « Dépasse le stock DC (DC 500 < 720) » et doit être confirmée (onglet « À vérifier », LT bloqué tant qu'elle ne l'est pas). C'est la seule exception à « modifier ne change jamais le statut » (D-039), demandée par le planner ; `apply_overrides` (`app/engines/overrides.py`) la porte, et la table lit désormais le statut de la ligne finale.
- **Parité Excel ne plafonne jamais** (Excel ne le fait pas) : 416/416 inchangé, vérifié ; l'override au-dessus du DC n'y change pas le statut non plus.
- Un SKU absent de l'export DC (stock inconnu) n'est pas plafonné : REVIEW « Stock DC insuffisant : SKU absent du stock DC » dans les deux modes (rien de silencieux, Q-033).
- Retiré : `dc_warn` / `dc_forced`, le statut « confirmer quand même ? », le lot qui excluait les lignes DC à 0, les traces `confirm_dc0` / `export_dc0` (les validations déjà enregistrées restent lisibles dans l'Historique). Outil installé et démo web ; tests `tests/unit/test_dc_cap.py` et test Streamlit ; CLAUDE.md mis à jour.

## D-052 — Colonne Expected = dispo + en route + Qty finale ; plus de « Expected + commande » (2026-10-13)

- Correction du planner : la colonne « Expected + commande » disparaît. La colonne **Expected** affiche Available + Incoming + Qty finale : Qty finale 0 → l'Expected de la Stock Situation ; dès qu'une quantité est saisie, Expected monte en direct et la Couverture suit. Quand la Qty finale > 0, la cellule prend un léger fond bleu pour dire qu'elle inclut la commande en cours. Info-bulle de la cellule (démo web) et ligne du « Pourquoi » (outil installé, Streamlit n'a pas d'info-bulle par cellule) : « dispo 3 720 + en route 4 080 + commande 3 600 ».
- **Affichage seulement** : le moteur part toujours de l'Expected de la Stock Situation (`inputs.expected_total`), parité 416/416 intacte, vérifiée.
- Colonnes finales : Confirmé · (Détail) · Statut · SKU · Description · Available · Expected · Ventes · Qty proposée · Qty finale · Couverture · Multiple · Stock DC · Raisons (Available passe avant Expected). Outil installé et démo web.

## D-053 — Date de fin facultative pour tous les événements SKU (2026-10-13)

- Demande du planner : pouvoir mettre une fenêtre de dates ou non. Exemple : une conversion de SKU est effective à partir d'une date, sans fin.
- La date de **début reste obligatoire**. La date de **fin est facultative pour tous les types** : vide = « sans fin », l'événement s'applique à partir du début (moins l'anticipation) jusqu'à ce que le planner le termine ou l'annule. Une fin avant le début reste refusée.
- Affichage : « sans fin » dans la colonne Fin de la table, dans le panneau et dans le formulaire (« Fin (vide = sans fin) ») ; « prochaine commande » pour une demande boutique à cette échéance. Pas de « Fin proche » ni de jours restants sans date de fin.
- Template Excel : colonne Fin vide acceptée pour tous les types (une ligne sans fin n'est plus rejetée à l'import).
- Aucun changement de moteur ni de base (la fin était déjà facultative pour Arrêt, Interdiction, allocation et stock cible). Parité 416/416 inchangée. Même fonctionnement dans la démo web, où un nouvel événement démarre sans date de fin.
