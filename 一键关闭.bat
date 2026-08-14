@echo off
chcp 65001 >nul
title 关闭 Outlook Register WebUI
cd /d "%~dp0"

echo ===================================================
echo   [*] 正在关闭 WebUI 服务...
echo ===================================================

REM 1. 优先通过 8765 端口强力查杀占用进程（极速精准）
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 0 } | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM 2. 双重保障：按 netstat 查杀
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a >nul 2>&1
)

REM 3. 按 PID 文件清理
if exist webui.pid (
    for /f "delims=" %%a in (webui.pid) do (
        taskkill /F /T /PID %%a >nul 2>&1
    )
    del webui.pid >nul 2>&1
)

echo.
echo   [OK] WebUI 服务已彻底关闭！
echo ===================================================
echo.
pause
