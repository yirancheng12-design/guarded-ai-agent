@echo off
setlocal
pushd "%~dp0" || exit /b 1
if defined SETUP_PYTHON goto custom
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto launcher
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto python
echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/
echo Enable the Python launcher or add Python to PATH, then rerun Install.cmd.
echo You can also set SETUP_PYTHON to the full path of your Python executable.
set "SETUP_RESULT=1"
goto finish
:custom
"%SETUP_PYTHON%" tools\install.py %*
set "SETUP_RESULT=%ERRORLEVEL%"
goto finish
:launcher
py -3 tools\install.py %*
set "SETUP_RESULT=%ERRORLEVEL%"
goto finish
:python
python tools\install.py %*
set "SETUP_RESULT=%ERRORLEVEL%"
:finish
popd
if not defined SETUP_NO_PAUSE pause
exit /b %SETUP_RESULT%
