@echo off
cd /d "%~dp0"
title Start WebUI Service
echo [*] 正在关闭旧进程并启动 WebUI ...
python -u start_webui.py
echo.
pause
