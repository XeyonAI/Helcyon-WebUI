@echo off
cd /d "%~dp0"

set "HWUI_F5_VENV="
set "HWUI_F5_VENV_READY="
if exist "%~dp0tts_paths.local.json" (
    for /f "usebackq delims=" %%V in (`powershell.exe -NoProfile -Command "$c = Get-Content -Raw -LiteralPath '%~dp0tts_paths.local.json' | ConvertFrom-Json; if ($c.HWUI_F5_VENV) { [Console]::Write($c.HWUI_F5_VENV.Replace('{build_dir}', (Resolve-Path -LiteralPath '%~dp0').Path)) }"`) do set "HWUI_F5_VENV=%%V"
)

if defined HWUI_F5_VENV (
    if exist "%HWUI_F5_VENV%\Scripts\activate.bat" (
        call "%HWUI_F5_VENV%\Scripts\activate.bat"
        set "HWUI_F5_VENV_READY=1"
    )
)
if not defined HWUI_F5_VENV_READY if exist "C:\HWUI-TTS\F5\venv\Scripts\activate.bat" (
    call "C:\HWUI-TTS\F5\venv\Scripts\activate.bat"
    set "HWUI_F5_VENV_READY=1"
)
if not defined HWUI_F5_VENV_READY (
    call venv\Scripts\activate.bat
)
python f5_server.py
pause
