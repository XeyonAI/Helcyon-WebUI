@echo off
setlocal

echo ================================================
echo  HWUI Master Launcher
echo ================================================

cd /d "%~dp0"

:: Activate HWUI's own Python environment
if not exist "venv\Scripts\activate.bat" (
    echo ERROR: HWUI virtual environment was not found.
    pause
    exit /b 1
)

call "venv\Scripts\activate.bat"

:: Read port from settings.json (falls back to 8081 if not set)
set "HWUI_PORT="
for /f %%p in ('python -c "import json; d=json.load(open('settings.json', encoding='utf-8')); print(d.get('port', 8081))"') do set "HWUI_PORT=%%p"

if not defined HWUI_PORT (
    echo ERROR: Could not determine HWUI port from settings.json.
    pause
    exit /b 1
)

:: Kill any existing process already listening on this port
echo Checking for existing processes on port %HWUI_PORT%...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%HWUI_PORT% " ^| findstr LISTENING') do (
    echo Killing old process: %%a
    taskkill /PID %%a /F >nul 2>&1
)

echo Clean start.
echo Starting HWUI on port %HWUI_PORT%...

:: Start HWUI in a separate process
start "HWUI Server" /B python app.py

:: Wait for the server port to start listening. Do not wait forever.
echo Waiting for HWUI to become ready...
set /a WAIT_COUNT=0

:WAIT_FOR_HWUI
python -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); rc=s.connect_ex(('127.0.0.1', int('%HWUI_PORT%'))); s.close(); sys.exit(0 if rc==0 else 1)" >nul 2>&1
if not errorlevel 1 goto OPEN_BROWSER

set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 60 (
    echo WARNING: HWUI did not report ready within 60 seconds.
    echo Opening the browser anyway so any server error is visible.
    goto OPEN_BROWSER
)

timeout /t 1 /nobreak >nul
goto WAIT_FOR_HWUI

:OPEN_BROWSER
echo Opening HWUI in your default browser...
powershell -NoProfile -Command "Start-Process 'http://127.0.0.1:%HWUI_PORT%/'" >nul 2>&1
if errorlevel 1 start "" "http://127.0.0.1:%HWUI_PORT%/"

endlocal
exit /b 0
