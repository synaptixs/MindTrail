@echo off
cd /d "%~dp0"
if exist ".venv-desktop\Scripts\pythonw.exe" (
  start "" ".venv-desktop\Scripts\pythonw.exe" "src/desktop.py"
  exit /b
)
echo Desktop dependencies are missing. Follow README - Desktop launcher to set up this source checkout.
pause
