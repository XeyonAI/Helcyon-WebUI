@echo off
cd /d "%~dp0"

if not exist "..\venv\Scripts\activate.bat" (
    echo HWUI's shared environment was not found at ..\venv.
    echo Run the HWUI setup first.
    pause
    exit /b 1
)

call "..\venv\Scripts\activate.bat"

streamlit run app.py

pause
