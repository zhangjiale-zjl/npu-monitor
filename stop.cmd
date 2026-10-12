@echo off
call "%~dp0start.cmd" -Stop %*
exit /b %ERRORLEVEL%
