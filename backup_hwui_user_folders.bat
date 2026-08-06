@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  HWUI User Data Backup
::  Backs up all user-specific folders into hwui_user.rar
::  Saved to the same directory as this script (overwrites)
:: ============================================================

cd /d "%~dp0"

set OUTFILE=%~dp0hwui_user.rar

echo.
echo  HWUI User Data Backup
echo  =====================
echo  Output: %OUTFILE%
echo.

:: --- Locate rar.exe ---
set RAR_EXE=

:: 1. Check PATH
where rar >nul 2>&1
if not errorlevel 1 (
    set RAR_EXE=rar
    goto :rar_found
)

:: 2. Common install locations
if exist "C:\Program Files\WinRAR\rar.exe"       set "RAR_EXE=C:\Program Files\WinRAR\rar.exe"
if exist "C:\Program Files (x86)\WinRAR\rar.exe" set "RAR_EXE=C:\Program Files (x86)\WinRAR\rar.exe"
if "!RAR_EXE!" neq "" goto :rar_found

:: 3. Registry lookup (handles non-standard install paths)
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\WinRAR" /v "exe64" 2^>nul') do set "RAR_EXE=%%b"
if "!RAR_EXE!" neq "" goto :rar_found
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\WinRAR" /v "exe64" 2^>nul') do set "RAR_EXE=%%b"
if "!RAR_EXE!" neq "" goto :rar_found

:: 4. Give up
echo  ERROR: rar.exe not found. Could not locate WinRAR automatically.
echo  Please add WinRAR to your PATH, or edit this script and hardcode the path.
pause
exit /b 1

:rar_found
echo  Using: !RAR_EXE!
echo.

:: --- Add folders ---
"!RAR_EXE!" a -r -ep1 -m3 -o+ "%OUTFILE%" ^
 "characters" ^
 "character_cards" ^
 "chats" ^
 "document uploads" ^
 "global_documents" ^
 "memories" ^
 "opening_lines" ^
 "projects" ^
 "session_summaries" ^
 "static" ^
 "system_prompts" ^
 "templates" ^
 "users"

:: --- Add system_prompt.txt only if it exists ---
if exist "system_prompt.txt" (
    echo  Adding system_prompt.txt...
    "!RAR_EXE!" a -ep1 -o+ "%OUTFILE%" "system_prompt.txt"
) else (
    echo  Skipping system_prompt.txt ^(not found^)
)

echo.
echo  Done! Backup saved to:
echo  %OUTFILE%
echo.
pause
