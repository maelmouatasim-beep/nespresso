# Questions ouvertes

Hypothèses métier non confirmées. Tant qu'une question est ouverte, le comportement
correspondant est signalé (REVIEW / BLOCKED) ou documenté, jamais appliqué en silence.

| # | Question | Comportement actuel | À qui |
|---|---|---|---|
| Q-001 | ~~Pourquoi `7010.70` est-il forcé à 0 dans Excel ?~~ **Résolue (A8)** : plus de SKU codé en dur, référentiel « Exclusions » éditable. | `7010.70` dans le référentiel Exclusions de la démo, BLOCKED avec sa raison (D-021) | — |
| Q-002 | ~~Comment Excel choisit-il les lignes de Stock Cover Final ?~~ **Résolue (A4)** : portfolio comparé en numérique puis en texte → les 451 lignes d'Excel sont retrouvées. Reste `2007.70`, voir Q-023. | Mode Parité : sélection Excel ; mode Standard : périmètre « activité » (A5, D-020) | — |
| Q-003 | Conversion : le new SKU doit-il additionner les ventes et l'Expected de l'old SKU **avant** la date de conversion (ex. 7969.70 ← 7863.70, effective 07-oct) ? Et quelles couvertures afficher au planner : totales (notre choix) ou new SKU seul (affichage Excel) ? | Addition toujours faite ; affichage des totaux (D-003) | Planner |
| Q-004 | Plusieurs old SKU vers un même new SKU : faut-il tous les additionner ? (Excel, via RECHERCHEV, n'en prendrait qu'un.) Aucun cas dans les données actuelles. | Tous additionnés, chacun tracé dans l'explication | Planner |
| Q-005 | Stock DC : quelle extraction fait foi, et à quelle heure ? 20 SKU diffèrent de +1 à +150 entre Excel et l'export Power BI (D-004). | Export Power BI fourni | Planner |
| Q-006 | Multiples à 0 dans la Multiple list (9 SKU : 80212, 93885, RC111-CA-…) : erreur de saisie ou « ne pas commander » ? | REVIEW ; qty 0 en parité, multiple traité comme manquant en safe (D-001) | Planner |
| Q-007 | ~~`7922.70` : 800 ou 240 ?~~ **Résolue (A9)** : café ORI → 800. Les **autres doublons** de la Multiple list (35 SKU) restent à trancher un par un. | REVIEW « Deux multiples » ; en Standard, le multiple de la famille l'emporte s'il est l'un des deux (D-022) | Planner |
| Q-008 | Espressos Vertuo à 360 : la détection se fait par nom dans la description (Altissio, Voltesso, Diavolitto, Orafio). Liste complète et exacte ? | Utilisée seulement pour inférer un multiple manquant (mode safe), toujours en REVIEW | Planner |
| Q-009 | SKU absents de la Stock Situation (ex. les 10 ajouts manuels 7938.70, 160421…) : Expected = 0 est-il correct ? | Expected 0 + REVIEW « SKU absent de la Stock Situation » | Planner |
| Q-010 | ~~Masquer les lignes « Aucune activité » ?~~ **Résolue (A5)** : rien n'est caché, les lignes à 0 sans alerte sont repliées. | Repliées sous chaque famille | — |
| Q-011 | Un old SKU pas encore bloqué (conversion à venir) doit-il encore être commandé ? | Calcul normal + REVIEW (D-009) | Planner |
| Q-012 | Codes de mouvement : faut-il un jour exclure les non-ventes (9044 Recycling, 8751 Tastings, 8749 Decoration…) ? Pas une décision à prendre maintenant. | Tous les codes inclus (défaut voulu) | Équipe planners |
| Q-013 | Hiérarchie d'arbitrage du brief : allocation > lancement > stock cible > prévision, est-ce correct ? (le brief n'est pas dans le dépôt) | Appliquée telle quelle (D-012) | Planner |
| Q-014 | Allocation : ~~arrondir au multiple ?~~ **Résolu (A3)** : toujours arrondie au multiple supérieur. Reste : comment répartir les vagues (ex. 75/25) dans le temps ? | Arrondi au multiple + raison ; vague choisie à la main | Planner |
| Q-015 | **Mécanique des lancements (ouverte, A10)** : la quantité initiale doit-elle déduire l'Expected ? Jusqu'à quelle date l'envoyer ? Faut-il plusieurs envois ? | Quantité − Expected, arrondie au multiple, jusqu'à la date de lancement incluse | Planner |
| Q-016 | Stock cible : par SKU (choix actuel) ou par famille / boutique (ex. « keep expected ~140K ») ? | Par SKU | Planner |
| Q-017 | ~~Noms exacts des colonnes de l'export Stock Movements brut~~ **Résolue** (vrai fichier du 07-oct-2026) : « Stock Movement Id, Stock, Product Nr, Product Type (Prod), Stock Mvt Date, Mvt Code, Quantity (Sum), Mvt Code Descr (Mvt Cd) », sorties en négatif. | Lu tel quel ; sorties comptées en ventes positives (D-026) | — |
| Q-018 | Noms exacts des onglets et en-têtes du calculateur `.xlsm` | Plusieurs noms acceptés ; `--inspect` pour les voir | Planner |
| Q-019 | Mode par défaut dans l'interface : Standard (ex-« Safe », choix actuel) ou Parité Excel ? | Standard | Équipe planners |
| Q-020 | Règles boutique : quels autres types faut-il (ex. « keep expected ~140K », « max 5 sacs S et M », « ajouter 4 caisses chaque lundi ») ? | 4 types : max_pallets, max_units, max_qty_sku, min_qty_sku ; signalement seulement | Planner |
| Q-021 | Catégories d'override : la liste (promo, lancement, rupture, surstock, stock DC, donnée erronée, autre) convient-elle ? | Liste actuelle | Équipe planners |
| Q-022 | **Deux créneaux de livraison le même jour de commande (ouverte, A10)** : lequel retenir pour la date suggérée ? | Le premier du Schedule ; la date est toujours confirmée par le planner | Planner |
| Q-023 | **`2007.70` absent d'Excel (ouverte, A10)** : VER-SBUX Blonde Espresso Roast a 40 ventes sur B80 mais n'est pas dans Stock Cover Final. Pourquoi ? | Présent dans notre sélection (452 lignes en Parité au lieu de 451), écart documenté (D-020) | Planner / analyse du .xlsm |
| Q-024 | Historique de secours : 4 semaines par défaut, est-ce la bonne valeur ? Faut-il ignorer les semaines de promo ? | 4 semaines complètes avec ventes, réglable dans ⚙️ | Équipe planners |
| Q-025 | L'export Stock Movements exclut déjà les codes 0502, 1508, 0064, 0062 et 0501 (filtre Power BI). Est-ce voulu, et le calculateur Excel les excluait-il aussi ? | Pris tels quels : l'outil compte tout ce qui est dans l'export | Planner |
| Q-026 | SKU avec un espace en trop dans Nessoft (ex. «  473ECO/B » à côté de « 473ECO/B ») : deux produits distincts ou erreur de saisie ? | Confondus une fois les espaces retirés ; si une seule ligne a du stock elle est gardée (avertissement), sinon fichier bloqué | Planner |
