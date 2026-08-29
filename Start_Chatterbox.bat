@echo off
echo ============================================
echo   Chatterbox TTS Server — Port 8004
echo ============================================
echo.

:: Set HuggingFace cache to I: drive (keeps weights off C:)
set HF_HOME=I:\HuggingFace
set TRANSFORMERS_CACHE=I:\HuggingFace

:: Activate HWUI venv
call venv\Scripts\activate.bat

:: Check chatterbox-tts is installed
python -c "import chatterbox" >nul 2>&1
if errorlevel 1 (
    echo [!] chatterbox-tts not found. Installing now...
    pip install chatterbox-tts
    echo.
)

echo [*] Starting Chatterbox TTS server...
echo [*] Model will load on first run — this may take a moment.
echo [*] Weights cached to: %HF_HOME%
echo.
python chatterbox_server.py
pause
