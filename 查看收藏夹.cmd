@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "NO_PROXY=*"
set "no_proxy=*"
"%~dp0.venv\Scripts\python.exe" "%~dp0douyin_collection_downloader.py" --list
echo.
pause
