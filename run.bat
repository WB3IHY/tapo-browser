@echo off
REM Tapo Camera Manager launcher (Windows).
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo ----------------------------------------------------------------
  echo  This app needs 'uv' ^(a small Python tool^) to run.
  echo  Install it once by opening PowerShell and running:
  echo.
  echo    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  echo.
  echo  Then close this window, open a new one, and run run.bat again.
  echo  More info: https://docs.astral.sh/uv/
  echo ----------------------------------------------------------------
  pause
  exit /b 1
)

REM uv provisions Python 3.13 + dependencies automatically on first run.
uv run --python 3.13 python -m tapo_cli
pause
