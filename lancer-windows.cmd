@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-windows\Scripts\python.exe" (
  echo Lancer installer-windows.cmd d'abord.
  pause
  exit /b 1
)
".venv-windows\Scripts\python.exe" ic7300_scope.py %*
if errorlevel 1 pause
