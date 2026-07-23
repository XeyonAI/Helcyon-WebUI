@echo off
echo ============================================
echo   Helcyon-WebUI Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.11 from python.org
    pause
    exit /b 1
)

echo [1/5] Creating virtual environment...
python -m venv venv

echo [2/5] Activating environment...
call venv\Scripts\activate.bat

echo [3/5] Installing PyTorch...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet

echo [4/5] Installing remaining dependencies...
pip install flask flask-cors requests psutil faster-whisper openai-whisper python-docx odfpy PyPDF2 --quiet

echo [5/5] Setting up model, TTS and config folders...

:: Create default models folder at C:\HWUI-Models
if not exist "C:\HWUI-Models" (
    mkdir "C:\HWUI-Models"
    echo Created models folder: C:\HWUI-Models
) else (
    echo Models folder already exists: C:\HWUI-Models
)

:: Drop a blank model_names.txt if one doesn't exist there
if not exist "C:\HWUI-Models\model_names.txt" (
    echo. > "C:\HWUI-Models\model_names.txt"
    echo Created blank model_names.txt in C:\HWUI-Models
)

:: Create predictable optional TTS folders. Engines remain optional and
:: install into their own environments; these are only their shared defaults.
for %%D in (
    "C:\HWUI-TTS\Cache\HuggingFace"
    "C:\HWUI-TTS\F5\voices"
    "C:\HWUI-TTS\F5\models"
    "C:\HWUI-TTS\Chatterbox\voices"
    "C:\HWUI-TTS\Qwen3-TTS\models\Qwen"
    "C:\HWUI-TTS\XTTS\voices"
) do (
    if not exist "%%~D" mkdir "%%~D"
)
echo TTS folders ready under C:\HWUI-TTS

:: Create settings.json from default if it doesn't exist
if not exist settings.json (
    if exist settings.default.json (
        copy settings.default.json settings.json >nul
        echo Created settings.json from defaults.
    )
)

echo.
echo ============================================
echo   Setup complete!
echo.
echo   Your models folder is: C:\HWUI-Models
echo   Drop your .gguf files there, then run
echo   START_UI.bat to launch.
echo.
echo   To change the models folder later, open
echo   the Config page inside HWUI.
echo ============================================
pause
