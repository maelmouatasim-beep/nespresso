#!/bin/bash
# Lance le Supply Planning Copilot (Mac / Linux). Double-clique sur ce fichier.
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3.12 || command -v python3)"
if [ -z "$PY" ]; then
  echo "Python 3.12 est introuvable : installe-le depuis https://www.python.org/downloads/"
  read -r -p "Appuie sur Entrée pour fermer."
  exit 1
fi
if [ ! -d .venv ]; then
  echo "Première installation : quelques minutes..."
  "$PY" -m venv .venv || exit 1
fi
echo "Vérification des dépendances..."
.venv/bin/python -m pip install -q -e . || { echo "Installation impossible (internet ?)"; exit 1; }
echo "L'outil s'ouvre dans ton navigateur. Ctrl+C ou ferme cette fenêtre pour l'arrêter."
.venv/bin/python -m streamlit run app/ui/main.py
