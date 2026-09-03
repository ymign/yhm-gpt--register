@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Close Outlook Register WebUI

echo [*] 正在关闭 WebUI 服务...
python start_webui.py --stop

echo.
pause
