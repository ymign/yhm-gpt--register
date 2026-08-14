@echo off
cd /d "%~dp0"

REM 1. 杀掉旧的 WebUI 进程
if exist webui.pid (
    for /f "delims=" %%a in (webui.pid) do taskkill /F /PID %%a >nul 2>&1
    del webui.pid >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

ping -n 2 127.0.0.1 >nul
del webui_console.log webui_error.log >nul 2>&1

REM 2. 后台启动，日志写文件，PID 存 webui.pid
powershell -NoProfile -Command "$p = Start-Process -FilePath 'python' -ArgumentList 'start_webui.py','--no-browser' -WorkingDirectory '%~dp0' -RedirectStandardOutput '%~dp0webui_console.log' -RedirectStandardError '%~dp0webui_error.log' -WindowStyle Hidden -PassThru; $p.Id | Set-Content -Path '%~dp0webui.pid'"

echo [OK] WebUI 已启动，访问 http://127.0.0.1:8765/
start "" http://127.0.0.1:8765/
exit