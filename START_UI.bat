@echo off
echo ================================================
echo  HWUI Master Launcher
echo ================================================

cd /d "%~dp0"

:: Read port from settings.json (falls back to 8081 if not set)
for /f %%p in ('python -c "import json; d=json.load(open('settings.json')); print(d.get('port', 8081))"') do set HWUI_PORT=%%p

:: Kill any existing process on that port before starting
echo Checking for existing processes on port %HWUI_PORT%...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%HWUI_PORT% ^| findstr LISTENING') do (
    echo Killing old process: %%a
    taskkill /PID %%a /F >nul 2>&1
)
echo Clean start.

echo Starting HWUI on port %HWUI_PORT%...
set HWUI_SCHEME=http
if exist "music.tail39b776.ts.net.crt" if exist "music.tail39b776.ts.net.key" set HWUI_SCHEME=https
start "" %HWUI_SCHEME%://127.0.0.1:%HWUI_PORT%
call venv\Scripts\activate
python app.py
pause
