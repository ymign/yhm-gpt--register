@echo off
cd /d "%~dp0"
title Close WebUI Service
python start_webui.py --stop
echo.
pause
