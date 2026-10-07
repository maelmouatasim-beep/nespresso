# Analyse de l'existant — constats vérifiés (05-oct-2026)

Ces constats viennent de l'analyse directe des fichiers suivants :

- B80 105.xlsm
- les deux exports Power BI « data - 2026-10-05… »
- Daily Orders 2026.xlsx
- le brief complet

## 1. Le calculateur Excel (B80 105.xlsm)

- 21 onglets, dont 17 masqués. Le cœur est **Stock Cover Final**, avec 461 lignes SKU pour B80.
- La formule de Suggested Qty a été **reproduite à l'identique sur 416/416 lignes** calculées par formule.
- Formule réelle : `qty = CEILING(cover × ventes / jours_historique − Expected, multiple)`, puis 0 si le résultat est ≤ 0.
- **La date de livraison n'entre pas dans le calcul.** « Sales days until delivery » (S3 = S4 − 3) ne sert qu'à l'affichage « Closing stk ».
- Les safety days de la Bible n'entrent pas dans la formule non plus. Le planner les intègre mentalement en choisissant le cover.
- **Reserved n'est pas déduit.** Vérifié sur l'export : `Expected = Available + Incoming − Waiting`.
- Les conversions sont gérées sur un seul niveau (old → new). Le nouveau SKU additionne les ventes et l'Expected de l'ancien. L'ancien est mis à 0 à partir de J+1 après la date de conversion. Les chaînes A → B → C ne sont pas gérées.
- Exception codée en dur : `7010.70` vaut toujours 0.
- **31 SKU de la liste n'ont pas de multiple.** La formule utilise alors 1 en silence. C'est le cas de lancements comme 7200.70, alors que la Bible indique un UOM de 240.
- Le stock DC n'est jamais plafonné : il est seulement marqué « INSUFFICIENT ».
- L'export Nessoft (macro) est un CSV à une colonne `SKU;QTY`, nommé `B80 05-Oct-2026-11_51.csv`. Le dossier de destination est sur OneDrive.
- Il existe aussi un « Recap » (onglet Order Data), envoyé aux boutiques avec l'estimation de palettes.

## 2. ⚠️ Le cas test du brief (Melozio 3 600) est un override manuel

- La cellule K2 contient la valeur 3600 tapée à la main, pas une formule.
- La formule aurait donné **3 120**, soit 2 935,7 arrondi au multiple de 240.
- **45 lignes sur 461 (environ 10 %) sont des overrides manuels** : lancements Festive, cafés en promo, machines, etc.
- Conséquence : la parité se teste sur les 416 lignes calculées par formule. Les 45 overrides sont précieux, car ils montrent exactement où le jugement du planner remplace la formule. Le nouvel outil doit les capturer, avec leur raison.

## 3. Ventes (onglet Sales = export Stock Movements)

- 1 551 mouvements pour B80 sur 7 jours, du 28-sept au 04-oct.
- **Tous les codes de mouvement sont additionnés**, dont certains qui ne sont pas des ventes clients :
  - 9044 Recycling Program : 68 lignes
  - 8751 BTQ Tastings
  - 8749 BTQ Decoration
  - 5045 Replace damaged goods
  - 5043 Replace due to Order Entry
- Codes de vente probables : 8730 BIM Order B2C, 1005 Internet, 2801 Last Chance BOGO, 2604 Winset, 9601 MGM, 1401 et 1001 Club, 1106 Welcome Offer, 9645 Best Buy M2M.
- L'historique n'est pas corrigé des ruptures : un SKU en rupture 3 jours sur 7 voit sa demande sous-estimée. La colonne « OOS Last 7 days » existe mais n'est pas utilisée dans le calcul.

## 4. Exports Power BI fournis (« data - … »)

- Les deux fichiers sont des **Stock Situation** (94 156 lignes, 42 emplacements, boutiques et DC). Ils sont identiques à 2 lignes près, ce qui est normal pour deux extractions du même jour.
- **Aucun export Stock Movements brut n'a été fourni.** Seul l'onglet Sales du calculateur en contient un, pour B80.
- Pièges à gérer à l'ingestion :
  - ligne de pied « No filters applied »
  - 38 Expected négatifs (Waiting > stock)
  - quelques Product Type vides
  - SKU au format texte variable (`7005.70`, `0005452`, `J620-US-ME-BV`, `127708/CA`)

## 5. La Bible (Daily Orders 2026.xlsx)

- 207 onglets : un par jour (de JAN 2 à OCT 9), plus 11 onglets « master ».
- Les feuilles du jour mélangent dans les mêmes colonnes :
  - lignes boutique (DC, code, transporteur, date de livraison, planner, notes)
  - promos par boutique
  - listes « do not replenish »
  - jours fériés
  - lancements (SKU, date, UOM, fichier source)
  - conversions écrites en texte libre (« Congo old 7872.70 - New 7934.70 »)
- Les allocations réelles sont dans **des fichiers externes non fournis** (FESTIVE COFFEE ALLOCATIONS.xlsx, Festive acc 2026.xlsx, etc.). On en trouve un exemple partiel dans SAMS NOTES (matrice SKU × boutique).
- Incohérences observées :
  - Les cover days par jour de commande diffèrent entre l'onglet Schedule du calculateur et MASTER BTQ INFO / MTL CD.
  - Les safety days sont parfois du texte (« 2.5- 3 », « Total 16-18 »).
  - « Promos Actives » n'est plus à jour (il s'arrête en juin-juillet).
  - La boutique BA9 (Pop Up Polo Park) apparaît, mais elle n'est ni dans DC Mapping ni dans le Schedule.
- Beaucoup de règles boutique sont en texte libre, par exemple « never exceed 5 S and M bags », « keep expected around 140K », « max 4 pallets », « add 4 medium cases every Monday ». Ce sont des candidates pour des règles structurées.
- **Données personnelles** (noms et emails d'employés, contacts transporteurs) : elles ne doivent jamais entrer dans le dépôt ni être envoyées à un LLM.

## 6. Conséquences sur le plan

1. **Phase 1** : reproduire exactement la formule Excel (parité 100 % sur les 416 lignes), puis ajouter les garde-fous (multiple absent → REVIEW, etc.) **en mode séparé**, pour ne pas casser la parité.
2. Rendre configurable la liste des codes de mouvement inclus. Par défaut : tous, pour la parité. L'option « ventes clients seulement » sera validée par l'équipe.
3. Capturer les overrides avec leur raison dès le MVP, car c'est la donnée la plus précieuse.
4. Lancements et allocations : il faut un modèle de fichier simple (SKU × boutique × qty × vague) plutôt que de parser les fichiers marketing hétérogènes.
5. Bible : commencer par une saisie structurée (tables Conversions, Launches, Notes boutique) et garder le parsing IA pour la phase 5.
