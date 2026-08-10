@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "NO_PROXY=*"
set "no_proxy=*"
"%~dp0.venv\Scripts\python.exe" "%~dp0douyin_collection_downloader.py"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo 程序退出代码：%EXIT_CODE%
pause
exit /b %EXIT_CODE%
