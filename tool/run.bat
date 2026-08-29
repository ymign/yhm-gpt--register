@echo off
cd /d "%~dp0"
title GPT 2FA Sub2API Tool
echo ======================================================================
echo   GPT 2FA Password Manager and Sub2API Converter
echo ======================================================================
echo.

python --version >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.9+ and add to PATH.
    pause
    exit /b 1
)

echo [1/2] Installing requirements...
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet

echo [2/2] Starting Web Server...
python app.py

pause
