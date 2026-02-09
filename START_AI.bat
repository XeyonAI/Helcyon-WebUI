@echo off
cd /d "%~dp0"
call venv\Scripts\activate
start http://127.0.0.1:8081
python app.py
pause