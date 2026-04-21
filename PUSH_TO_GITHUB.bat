@echo off
cd /d "D:\HWUI-1.0-Free\Helcyon-WebUI"

echo.
echo ========================================
echo   HWUI - Push to GitHub
echo ========================================
echo.

git add -A
git commit -m "HWUI update" --allow-empty

echo.
echo Pushing to GitHub...
git push --force

echo.
echo ========================================
echo   Done! Check GitHub to confirm.
echo ========================================
echo.
pause
