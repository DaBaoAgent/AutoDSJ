@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先安装 Python 3.11 或更高版本。
  pause
  exit /b 1
)

python launch_dabaoai.py
if errorlevel 1 (
  echo.
  echo DaobaoAI-DY 启动失败，请查看上方错误信息。
  pause
)
