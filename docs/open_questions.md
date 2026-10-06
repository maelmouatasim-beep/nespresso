# Questions ouvertes

Hypothèses métier non confirmées. Tant qu'une question est ouverte, le comportement
correspondant est signalé (REVIEW / BLOCKED) ou documenté, jamais appliqué en silence.

| # | Question | Comportement actuel | À qui |
|---|---|---|---|
| Q-001 | Pourquoi `7010.70` est-il forcé à 0 dans Excel ? Faut-il garder cette exception, et pour toutes les boutiques ? | Qty 0 + BLOCKED « Exception Excel codée en dur » (D-008) | Planner |
| Q-002 | Comment Excel choisit-il les lignes de Stock Cover Final ? La règle « Filter out » de CLAUDE.md ne colle pas : `2007.70` (VER-SBUX Blonde Espresso Roast, 40 ventes) n'apparaît pas dans Excel, alors que 54 SKU sans ventes ni stock et absents du portfolio y sont (ex. 7039.70, 7990.70, 1050.70). | Le test de parité reprend la liste des SKU du golden (D-007) | Planner / analyse du .xlsm |
| Q-003 | Conversion : le new SKU doit-il additionner les ventes et l'Expected de l'old SKU **avant** la date de conversion (ex. 7969.70 ← 7863.70, effective 07-oct) ? Et quelles couvertures afficher au planner : totales (notre choix) ou new SKU seul (affichage Excel) ? | Addition toujours faite ; affichage des totaux (D-003) | Planner |
| Q-004 | Plusieurs old SKU vers un même new SKU : faut-il tous les additionner ? (Excel, via RECHERCHEV, n'en prendrait qu'un.) Aucun cas dans les données actuelles. | Tous additionnés, chacun tracé dans l'explication | Planner |
| Q-005 | Stock DC : quelle extraction fait foi, et à quelle heure ? 20 SKU diffèrent de +1 à +150 entre Excel et l'export Power BI (D-004). | Export Power BI fourni | Planner |
| Q-006 | Multiples à 0 dans la Multiple list (9 SKU : 80212, 93885, RC111-CA-…) : erreur de saisie ou « ne pas commander » ? | REVIEW ; qty 0 en parité, multiple traité comme manquant en safe (D-001) | Planner |
| Q-007 | `7922.70` apparaît deux fois dans la Multiple list (800 et 240). Quel est le bon multiple ? | 800 (première ligne, comme Excel) | Planner |
| Q-008 | Espressos Vertuo à 360 : la détection se fait par nom dans la description (Altissio, Voltesso, Diavolitto, Orafio). Liste complète et exacte ? | Utilisée seulement pour inférer un multiple manquant (mode safe), toujours en REVIEW | Planner |
| Q-009 | SKU absents de la Stock Situation (ex. les 10 ajouts manuels 7938.70, 160421…) : Expected = 0 est-il correct ? | Expected 0 + REVIEW « SKU absent de la Stock Situation » | Planner |
| Q-010 | Faut-il masquer les lignes « Aucune activité » (ventes 0, Expected 0) dans l'interface ? 201 lignes sur 461 pour B80. | Affichées, statut OK, raison « Aucune activité » | Planner (étape interface) |
| Q-011 | Un old SKU pas encore bloqué (conversion à venir) doit-il encore être commandé ? | Calcul normal + REVIEW (D-009) | Planner |
| Q-012 | Codes de mouvement : faut-il un jour exclure les non-ventes (9044 Recycling, 8751 Tastings, 8749 Decoration…) ? Pas une décision à prendre maintenant. | Tous les codes inclus (défaut voulu) | Équipe planners |
