@echo off
cd /d "%~dp0"
if exist "C:\HWUI-TTS\F5\venv\Scripts\activate.bat" (
    call "C:\HWUI-TTS\F5\venv\Scripts\activate.bat"
) else (
    call venv\Scripts\activate.bat
)
python f5_server.py
pause
