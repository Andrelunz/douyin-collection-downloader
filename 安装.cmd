@echo off
chcp 65001 >nul
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo 未找到 uv，无法安装。
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" uv venv --python 3.11 .venv
if errorlevel 1 goto :fail
uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m unittest discover -s tests -q
if errorlevel 1 goto :fail
echo.
echo 安装与测试完成。双击“下载指定收藏夹.cmd”即可使用。
pause
exit /b 0
:fail
echo.
echo 安装失败，请保留此窗口中的错误信息。
pause
exit /b 1
