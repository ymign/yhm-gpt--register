@echo off
cd /d "%~dp0"
title Start WebUI Service
python start_webui.py
echo.
pause
