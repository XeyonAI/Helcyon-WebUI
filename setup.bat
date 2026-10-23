@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo ============================================
echo   Helcyon-WebUI Free Setup
echo ============================================
echo.

:: Check Python (3.11 recommended; 3.10 - 3.12 are supported)
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.11 from python.org
    echo        and tick "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.10 or newer is required. Please install Python 3.11 from python.org
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo WARNING: Python newer than 3.12 detected. Some audio/speech packages may not have
    echo          wheels for it yet. Python 3.11 is the tested version.
    echo.
)

if not exist requirements.txt (
    echo ERROR: requirements.txt is missing. Re-clone or re-download the full repository.
    pause
    exit /b 1
)

echo [1/7] Creating virtual environment...
if exist "venv\Scripts\python.exe" (
    echo Virtual environment already exists - reusing it.
) else (
    python -m venv venv
    if errorlevel 1 goto :fail_venv
)

echo [2/7] Activating environment...
call venv\Scripts\activate.bat
if errorlevel 1 goto :fail_venv
python -m pip install --upgrade pip --quiet --disable-pip-version-check

echo [3/7] Checking PyTorch...
:: An existing PyTorch that already works on this GPU is left alone. It is only (re)installed
:: when it is missing, CPU-only, or built without kernels for this GPU's architecture.
python scripts\check_env.py torch
set TORCH_RC=%errorlevel%
if "%TORCH_RC%"=="0" (
    echo Keeping the existing PyTorch installation.
    goto :torch_done
)
set "NVSMI="
where nvidia-smi >nul 2>&1
if not errorlevel 1 set "NVSMI=1"
if "%TORCH_RC%"=="3" if not defined NVSMI (
    echo No NVIDIA GPU detected - keeping the existing PyTorch build.
    goto :torch_done
)
:: CUDA build selection. RTX 50-series (compute capability 12.x) needs cu128 or newer;
:: older GPUs use cu121. Override by setting HWUI_CUDA_TAG before running.
if defined HWUI_CUDA_TAG goto :tag_done
set "HWUI_CUDA_TAG=cu121"
if not defined NVSMI goto :tag_done
:: Only a purely numeric major version counts; old drivers print an error text instead.
for /f "usebackq tokens=1 delims=." %%A in (`nvidia-smi --query-gpu^=compute_cap --format^=csv^,noheader 2^>nul`) do (
    echo %%A| findstr /r "^[0-9][0-9]*$" >nul && if %%A GEQ 12 set "HWUI_CUDA_TAG=cu128"
)
:tag_done
echo Installing PyTorch (%HWUI_CUDA_TAG%) - this is a large download...
if "%TORCH_RC%"=="3" (
    rem Replace only the mismatched torch packages; keep everything else in place.
    pip install --upgrade --force-reinstall --no-deps torch torchvision torchaudio --index-url https://download.pytorch.org/whl/%HWUI_CUDA_TAG% --disable-pip-version-check
) else (
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/%HWUI_CUDA_TAG% --disable-pip-version-check
)
if errorlevel 1 goto :fail_torch
:torch_done

echo [4/7] Installing remaining dependencies (requirements.txt)...
pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto :fail_deps

echo [5/7] Setting up model, TTS and config folders...

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

echo [6/7] Checking integrated Benchmark data...

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

echo [7/7] Verifying the installation...
python scripts\check_env.py core
if errorlevel 1 (
    echo.
    echo ERROR: Some required packages are missing or failed to import ^(listed above^).
    echo Fix the cause, then run setup.bat again - it is safe to re-run.
    pause
    exit /b 1
)

:: Prerequisites pip cannot install. Warnings only - HWUI starts without them.
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo NOTE: FFmpeg was not found on PATH. Voice input ^(Whisper^) needs it to read audio.
    echo       Then open a new terminal so PATH refreshes.
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
echo.
echo   Optional voice engines ^(Qwen3-TTS-Fast, native
echo   qwentts.cpp, F5, Chatterbox, XTTS, Kokoro^) are
echo   separate; see the docs folder.
echo ============================================
pause
exit /b 0

:fail_venv
echo.
echo ERROR: Could not create or activate the virtual environment.
echo If a previous setup was interrupted, delete the "venv" folder and run setup again.
pause
exit /b 1

:fail_torch
echo.
echo ERROR: PyTorch installation failed ^(see the pip output above^).
echo Check your internet connection and that Python is 64-bit 3.10 - 3.12, then run setup again.
pause
exit /b 1

:fail_deps
echo.
echo ERROR: Installing requirements.txt failed ^(see the pip output above^).
echo Fix the cause ^(network, disk space, antivirus blocking pip^), then run setup again.
pause
exit /b 1
