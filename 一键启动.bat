@echo off
chcp 65001 >nul
title Outlook Register WebUI 控制台
cd /d "%~dp0"

echo ===================================================
echo   [*] 正在检查并清理旧进程...
echo ===================================================

powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 0 } | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a >nul 2>&1
)

if exist webui.pid (
    for /f "delims=" %%a in (webui.pid) do taskkill /F /T /PID %%a >nul 2>&1
    del webui.pid >nul 2>&1
)

echo   [*] 正在启动 WebUI 服务...
echo   [*] 本地访问地址: http://127.0.0.1:8765/
echo ===================================================
echo.

python start_webui.py

echo.
echo ===================================================
echo   [!] WebUI 服务已停止。
echo ===================================================
pause
