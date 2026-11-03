@echo off
cd /d "%~dp0"
if exist ".venv-desktop\Scripts\pythonw.exe" (
  start "" ".venv-desktop\Scripts\pythonw.exe" "scripts/run-local.py" desktop
  exit /b
)
echo Desktop dependencies are missing. Follow README - Desktop launcher to set up this source checkout.
pause
