@echo off
REM Lance le Supply Planning Copilot (Windows). Double-clique sur ce fichier.
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3.12
if not exist .venv (
  echo Premiere installation : quelques minutes...
  %PY% -m venv .venv
  if errorlevel 1 goto nopython
)
echo Verification des dependances...
.venv\Scripts\python -m pip install -q -e .
if errorlevel 1 goto pipfail
echo L'outil s'ouvre dans ton navigateur. Ferme cette fenetre pour l'arreter.
.venv\Scripts\python -m streamlit run app\ui\main.py
pause
goto :eof

:nopython
echo Python 3.12 est introuvable.
echo Installe-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH",
echo puis double-clique a nouveau sur ce fichier.
pause
goto :eof

:pipfail
echo L'installation des dependances a echoue (connexion internet ? proxy ?).
pause
