@echo off
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Create the local environment first. See README.md or README.zh-CN.md.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0launch.pyw"
