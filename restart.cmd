@echo off
call "%~dp0start.cmd" -Restart %*
exit /b %ERRORLEVEL%
