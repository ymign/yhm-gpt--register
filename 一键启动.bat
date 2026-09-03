@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Outlook Register WebUI

python start_webui.py
echo.
pause
