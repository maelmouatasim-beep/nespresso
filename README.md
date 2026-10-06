# Supply Planning Copilot

Outil interne de planification des commandes boutiques (Nespresso Canada).
Règles du projet : voir [CLAUDE.md](CLAUDE.md).

## Installation (une fois)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Vérifier

```bash
pytest                       # tous les tests
pytest tests/parity -v -s    # parité avec Excel (affiche le score)
ruff check . && ruff format --check .
```

## Structure

- `app/domain/` : modèles de données (SKU toujours en texte)
- `app/engines/` : calculs purs (ventes, conversions, quantités)
- `app/ingestion/` : lecture des fichiers
- `tests/unit/`, `tests/parity/` : tests ; rapport des overrides dans `tests/parity/reports/`
- `docs/` : analyse, journal des décisions, questions ouvertes, dictionnaire de données

## Aperçu de test (HTML)

```bash
python tools/build_preview.py   # écrit preview/apercu_B80.html, à ouvrir dans un navigateur
```

Les quantités de l'aperçu sont pré-calculées par le moteur Python ; la page ne fait que les afficher.
Le dossier `preview/` n'est pas versionné.
