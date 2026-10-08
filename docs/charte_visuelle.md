# Charte visuelle

Obligatoire pour l'outil installé (Streamlit) ET la démo web. Thème **clair uniquement** :
le mode sombre est désactivé (`base = "light"` dans `.streamlit/config.toml`, `color-scheme: light`
dans la page web), même si l'ordinateur est réglé en sombre.

## Couleurs

| Rôle | Couleur | Où |
|---|---|---|
| Fond | `#FAF7F2` crème très clair | page |
| Cartes | `#FFFFFF` + ombre légère | tuiles, panneaux, tableaux |
| Texte | `#2B1D14` brun foncé | partout |
| Texte secondaire | `#6B5B4E` | libellés, aides (6,5:1 sur blanc) |
| Principal | `#3D2B1F` brun espresso | barre latérale, en-têtes de tableau |
| Accent doré | `#B08D57` | boutons principaux (texte brun foncé), survol, sélection, filets |
| Or « encre » | `#8A6A3A` | titres de section et liens (texte doré lisible) |
| Bordures, lignes | `#E8E1D8` gris chaud | |
| Lignes alternées | `#FBF8F3` | tableaux |

## Statuts (pastille + texte)

| Statut | Pastille | Texte | Fond | Contraste texte |
|---|---|---|---|---|
| OK | `#2E7D4F` | `#2A7449` | `#E6F2EA` | 4,9:1 |
| REVIEW | `#B7791F` | `#94600F` | `#FFF4DF` | 4,9:1 |
| BLOCKED | `#B23A3A` | `#B23A3A` | `#FBE9E7` | 5,0:1 |
| Modifié par le planner | `#2F5F9E` | `#2F5F9E` | `#E8F0FA` | 5,6:1 |

## Contrastes vérifiés (WCAG : 4,5:1 pour le texte normal)

- Texte `#2B1D14` sur crème : 15,3:1 ; sur blanc : 16,3:1.
- Texte crème sur espresso : 12,6:1.
- Bouton doré : texte **brun foncé** sur `#B08D57` = 5,3:1 (le blanc ne ferait que 3,1:1).
- L'or `#B08D57` comme texte sur crème ne fait que 2,9:1 : il n'est donc jamais utilisé pour du
  texte, seulement pour des fonds et des filets. Les titres dorés utilisent `#8A6A3A` (4,7:1).
- Les teintes d'origine du vert OK (`#2E7D4F`, 4,4:1) et de l'ambre REVIEW (`#B7791F`, 3,3:1)
  restent sur les pastilles ; le texte utilise une teinte un peu plus foncée de la même couleur.

## Typographie et mise en page

- Inter (sinon la police système : Segoe UI sous Windows), titres en graisse moyenne (600),
  chiffres en tabulaire pour que les colonnes s'alignent.
- Tuiles arrondies (10 px) : grande valeur, petit libellé. Espaces généreux en haut, tableau dense.
- Survol doré discret, icônes fines (trait fin, même famille partout), logo texte
  « Supply Planning Copilot » en haut à gauche. Pas de dégradé, pas d'image décorative.
- Limite de Streamlit : le tableau de l'outil installé est dessiné par le navigateur (canvas) ;
  son survol garde la couleur de Streamlit. Lignes alternées et pastilles de statut, elles, sont appliquées.
