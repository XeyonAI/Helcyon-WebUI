@echo off
echo ============================================
echo   HWUI-Pro Setup
echo ============================================
echo.
:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.11 from python.org
    pause
    exit /b 1
)
echo [1/4] Creating virtual environment...
python -m venv venv
echo [2/4] Activating environment...
call venv\Scripts\activate.bat
echo [3/4] Installing PyTorch...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet
echo [4/4] Installing remaining dependencies...
pip install flask flask-cors requests psutil faster-whisper openai-whisper --quiet
echo.
:: First-run: create settings.json from default if it doesn't exist
if not exist settings.json (
    if exist settings.default.json (
        copy settings.default.json settings.json >nul
        echo Created settings.json from defaults.
    )
)
echo ============================================
echo   Setup complete! Run Start_AI.bat to launch.
echo ============================================
pause