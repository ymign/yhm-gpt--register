@echo off
cd /d "%~dp0"
python start_webui.py --stop
echo 按任意键退出...
pause >nul
