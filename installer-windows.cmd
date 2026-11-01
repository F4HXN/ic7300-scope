@echo off
setlocal
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Installer Python 3.12 64 bits depuis python.org, avec le lanceur py.
  pause
  exit /b 1
)
py -3.12 -m venv .venv-windows
if errorlevel 1 goto :error
".venv-windows\Scripts\python.exe" -m pip install -r requirements-windows.txt
if errorlevel 1 goto :error
echo Installation terminee. Ouvrir lancer-windows.cmd pour demarrer.
pause
exit /b 0
:error
echo Installation interrompue. Voir le message ci-dessus.
pause
exit /b 1
