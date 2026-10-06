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
