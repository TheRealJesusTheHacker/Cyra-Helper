@echo off
REM Cyra Helper v1 — local Windows build.
REM Produces dist\CyraHelper\CyraHelper.exe and zips it for release.
REM Note: the .exe is UNSIGNED. Windows SmartScreen will warn on first run —
REM click "More info" then "Run anyway".

setlocal
cd /d "%~dp0\.."

where python >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 is required. Install it from https://www.python.org/downloads/
    exit /b 1
)

if not exist .venv (
    echo Creating venv...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Installing requirements...
pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo Installing PyInstaller...
pip install pyinstaller
if errorlevel 1 exit /b 1

echo Building...
pyinstaller packaging\cyra-helper.spec --noconfirm --clean
if errorlevel 1 exit /b 1

if not exist dist\CyraHelper\CyraHelper.exe (
    echo BUILD FAILED: dist\CyraHelper\CyraHelper.exe not found.
    exit /b 1
)

set /p VER=<helper\VERSION
echo Zipping dist\CyraHelper to dist\CyraHelper-%VER%-win64.zip ...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\CyraHelper\*' -DestinationPath 'dist\CyraHelper-%VER%-win64.zip' -Force"
if errorlevel 1 exit /b 1

echo.
echo Done: dist\CyraHelper-%VER%-win64.zip
echo Remember: unsigned binary — SmartScreen will warn on first run.
endlocal
