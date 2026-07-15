@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if "%~1"=="" (
  "%PY%" autodsj.py run
) else (
  "%PY%" autodsj.py %*
)

if errorlevel 1 (
  echo.
  echo [AutoDSJ 工作流] 执行失败，请查看上方错误信息。
  pause
)
