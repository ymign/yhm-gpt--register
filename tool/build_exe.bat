@echo off
cd /d "%~dp0"
title Build GPT 2FA Sub2API Tool
echo ======================================================================
echo   Building Standalone EXE with PyInstaller...
echo ======================================================================
echo.

python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet

echo Packaging...
pyinstaller --noconfirm --onedir --windowed ^
    --name "GPT_2FA_Sub2API_Tool" ^
    --add-data "static;static" ^
    --add-data "core;core" ^
    app.py

echo.
echo ======================================================================
echo   Build finished! Files located in: dist/GPT_2FA_Sub2API_Tool
echo ======================================================================
pause
