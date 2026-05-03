@echo off
echo ================================================
echo  HWUI Master Launcher
echo ================================================

cd /d "%~dp0"

:: Kill any existing process on port 8081 before starting
echo Checking for existing processes on port 8081...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8081 ^| findstr LISTENING') do (
    echo Killing old process: %%a
    taskkill /PID %%a /F >nul 2>&1
)
echo Clean start.

echo Starting HWUI...
start "" http://127.0.0.1:8081
call venv\Scripts\activate
python app.py
pause