@echo off
echo ============================================
echo   Helcyon-WebUI Free Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.11 from python.org
    pause
    exit /b 1
)

echo [1/6] Creating virtual environment...
python -m venv venv

echo [2/6] Activating environment...
call venv\Scripts\activate.bat

echo [3/6] Installing PyTorch...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet

echo [4/6] Installing remaining dependencies...
pip install flask flask-cors requests psutil faster-whisper openai-whisper python-docx odfpy PyPDF2 PyYAML --quiet

echo [5/6] Setting up model, TTS and config folders...

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

echo [6/6] Checking integrated Benchmark data...

:: app.py always loads the Helcyon-Bench blueprint, which imports the bundled
:: llmbench package. If this bundled data is missing, HWUI will not start at
:: all, not just the Benchmark tab. Fail here with a clear message instead.
set BENCH_OK=1
if not exist "helcyon-bench\llmbench" set BENCH_OK=0
if not exist "helcyon-bench\prompt_packs" set BENCH_OK=0
if not exist "helcyon-bench\rubrics" set BENCH_OK=0
if not exist "helcyon-bench\config.example.yaml" set BENCH_OK=0
if "%BENCH_OK%"=="0" (
    echo.
    echo ERROR: Integrated Benchmark data was not found under helcyon-bench\.
    echo Helcyon-WebUI requires this folder ^(llmbench, prompt_packs, rubrics, config.example.yaml^)
    echo to start. Re-clone or re-download the full repository and run setup again.
    pause
    exit /b 1
)
echo Integrated Benchmark data found.

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
