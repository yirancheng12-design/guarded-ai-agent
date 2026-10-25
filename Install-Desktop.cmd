@echo off
call "%~dp0Install.cmd" --profile desktop %*
exit /b %ERRORLEVEL%
