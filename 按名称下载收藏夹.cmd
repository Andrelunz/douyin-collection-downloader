@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到虚拟环境，请先运行 安装.cmd
  pause
  exit /b 1
)
set /p "COLLECTION=请输入收藏夹完整名称："
if "%COLLECTION%"=="" exit /b 0
set "NO_PROXY=*"
set "no_proxy=*"
"%~dp0.venv\Scripts\python.exe" "%~dp0douyin_collection_downloader.py" --collection "%COLLECTION%"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo 程序退出代码：%EXIT_CODE%
pause
exit /b %EXIT_CODE%
