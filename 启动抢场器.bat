@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw -m sjtu_tennis_toolkit --rush
    exit /b
)

where python >nul 2>nul
if not errorlevel 1 (
    start "" python -m sjtu_tennis_toolkit --rush
    exit /b
)

echo Could not find Python in PATH.
echo Please install Python or open PowerShell in this folder and run: python -m sjtu_tennis_toolkit --rush
pause

