@echo off
cd /d "I:\HWUI-Pro-Dev-build"
call I:\F5-TTS\f5_venv\Scripts\activate.bat
python f5_server.py
pause