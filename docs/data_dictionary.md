# Dictionnaire de données

Fixtures de test : `tests/fixtures/b80_2026-10-05/`. Tous les CSV sont lus **en texte**
(`dtype=str`), puis les colonnes numériques sont converties explicitement. Une valeur
numérique vide ou illisible lève une `SchemaError` (jamais remplacée par 0).

## stock_situation_B80_CY1.csv — export Power BI « Stock Situation »

Lignes de la boutique (B80) et de son DC (CY1). Modèle : `StockSnapshotLine`.

| Colonne | Champ | Type | Remarque |
|---|---|---|---|
| Stock | location | texte | Code boutique ou DC |
| Product | sku | texte | SKU, jamais converti en nombre |
| Product Descr | description | texte | `VER-…` = Vertuo, `ORI-…` = Original |
| Product Type | product_type | texte | C café, M machine, LC/A/AP/D/FB/… ; parfois vide |
| Expected | expected | nombre | = Available + Incoming − Waiting (Reserved NON déduit). Peut être négatif |
| Available | available | nombre | Côté DC : stock affiché « DC stock » |
| Incoming | incoming | nombre | |
| Reserved | reserved | nombre | Non utilisé dans le calcul |
| Waiting | waiting | nombre | |

## stock_movements_B80.csv — onglet Sales (export Stock Movements)

Modèle : `StockMovement`. 1 551 lignes, 7 dates (28-sept → 04-oct).

| Colonne | Type | Remarque |
|---|---|---|
| movement_id | texte | |
| location | texte | Boutique |
| sku | texte | |
| product_type | texte | |
| movement_date | date ISO | Le nombre de dates distinctes = `sales_history_days` |
| movement_code | texte | Tous les codes comptent par défaut (ventes ET déstockages) |
| quantity | nombre | Quantité sortie (positive) |
| movement_description | texte | |

### Export Power BI brut (vérifié le 07-oct-2026 sur un vrai fichier)

Fichier `.xlsx` nommé « data - AAAA-MM-JJThhmmss.mmm.xlsx », un onglet `Export`, toutes les
boutiques et les DC, ~80 000 lignes sur 7 jours, ligne de pied « Applied filters: … » (retirée).
L'outil reconnaît le fichier par ses colonnes, pas par son nom.

| Colonne de l'export | Champ | Remarque |
|---|---|---|
| Stock Movement Id | movement_id | texte |
| Stock | location | boutique ou DC |
| Product Nr | sku | texte |
| Product Type (Prod) | product_type | |
| Stock Mvt Date | movement_date | date Excel |
| Mvt Code | movement_code | texte (ex. « 2801 ») |
| Quantity (Sum) | quantity | **négatif** : l'export ne garde que les sorties (filtre « ≤ 0 ») ; l'outil les compte en ventes positives (D-026) |
| Mvt Code Descr (Mvt Cd) | movement_description | |

L'export Stock Situation brut a les mêmes colonnes que le fichier de test ci-dessus (onglet
`Export`, ~94 000 lignes, 42 emplacements dont les 4 DC, pied « No filters applied »).

## master_multiples.csv — onglet Multiple list

Modèle : `Product`. Première ligne gagnante en cas de doublon (D-005).

| Colonne | Type | Remarque |
|---|---|---|
| sku | texte | 3 SKU ont des espaces parasites |
| description | texte | Souvent vide |
| type | texte | |
| order_multiple | entier | Vide = absent ; 0 existe (9 SKU) |
| units_per_pallet | nombre | Pour le calcul de palettes (étape ultérieure) |

## master_sku_conversions.csv — onglet SKU Conversion

Modèle : `SkuConversion`. `new_sku = XYZ123` → pas de remplaçant, ne plus envoyer.

| Colonne | Type |
|---|---|
| old_sku | texte |
| new_sku | texte |
| effective_date | date ISO |
| comment | texte |

## master_dc_mapping.csv — onglet DC Mapping

`boutique` → `dc` (MT1, TO1, CY1, VA1).

## master_boutique_portfolio_B80.csv — onglet Boutique Portfolio

`boutique`, `sku`, `filter_out` (Yes/No). Chargé, pas encore utilisé (Q-002).

## master_schedule.csv — onglet Schedule

Jours de commande et de livraison. **Ses cover days ne sont jamais utilisés** : le planner les saisit à chaque run.

## parameters.json — cellules S3:S8 de Stock Cover Final

`boutique`, `dc`, `run_date`, `cover_days`, `coffee_cover_days` (null = pas de cover café), `sales_history_days` (vérification seulement : le moteur le recalcule depuis les mouvements), `sales_days_until_delivery` (affichage seulement), `hardcoded_zero_skus`.

## golden_stock_cover_final_B80.csv — résultat Excel attendu

**Jamais utilisé comme entrée.** Sert uniquement à comparer. `excel_cell_is_formula = False` = override manuel du planner (45 lignes).

## RecommendationLine — sortie du moteur

| Champ | Description |
|---|---|
| sku, description, product_type | Identité du produit |
| qty | Quantité suggérée (entier ≥ 0) |
| qty_reliable | False si calculée avec un multiple par défaut |
| status | OK / REVIEW / BLOCKED |
| reasons | Raisons en clair |
| explanation | Trace : `rule_version`, `mode`, `sources`, `parameters`, `inputs` (ventes/Expected propres, venant des old SKU, totaux, stock DC), `cover_applied`, `raw_need`, `multiple`, `multiple_source`, `qty`, `current_cover_days`, `post_cover_days`, `rules_triggered` |
