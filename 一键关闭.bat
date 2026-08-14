@echo off
cd /d "%~dp0"

echo [*] 正在关闭 WebUI ...

if exist webui.pid (
    for /f "delims=" %%a in (webui.pid) do taskkill /F /PID %%a >nul 2>&1
    del webui.pid >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

echo [OK] 已全部关闭
exit