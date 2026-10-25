@echo off
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Run Install.cmd first, or Install-Desktop.cmd for desktop and browser features.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0launch.pyw"
