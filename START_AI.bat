@echo off
echo ================================================
echo  HWUI Master Launcher
echo ================================================

echo Starting HWUI...
cd /d "%~dp0"
start "" "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" http://127.0.0.1:8081
call venv\Scripts\activate
python app.py
pause