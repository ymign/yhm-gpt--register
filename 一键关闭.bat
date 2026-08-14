@echo off
cd /d "%~dp0"
title Close Outlook Register WebUI
python start_webui.py --stop
echo.
pause
