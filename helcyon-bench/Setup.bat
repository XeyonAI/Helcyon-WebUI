@echo off
setlocal
cd /d "%~dp0"

echo.
echo Helcyon-Bench setup using HWUI's shared environment
echo ====================================================
echo.

set "HWUI_PYTHON=..\venv\Scripts\python.exe"
if not exist "%HWUI_PYTHON%" (
    echo HWUI's environment was not found at ..\venv.
    echo Run the HWUI setup first, then run this file again.
    echo.
    pause
    exit /b 1
)

"%HWUI_PYTHON%" --version >nul 2>nul
if errorlevel 1 (
    echo HWUI's environment exists but is not usable.
    echo Repair the HWUI environment first; Helcyon-Bench will not create a second venv.
    echo.
    pause
    exit /b 1
)

echo Installing Helcyon-Bench dependencies into HWUI's environment...
"%HWUI_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install Helcyon-Bench dependencies.
    pause
    exit /b 1
)

if not exist "config.yaml" if exist "config.example.yaml" (
    copy "config.example.yaml" "config.yaml" >nul
)

echo.
echo Setup complete. Helcyon-Bench now uses HWUI's shared venv.
echo.
pause
