@echo off
rem Starts Saydown with a console that shows each dictation, raw and cleaned. Close it or press Ctrl+C to quit.
cd /d "%~dp0"
where uv >nul 2>nul || (
  echo Saydown needs uv. Install it with:  winget install --id astral-sh.uv
  pause
  exit /b 1
)
uv run saydown.py
if errorlevel 1 pause
