@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "NPU_MONITOR_EXIT=%ERRORLEVEL%"
if not "%NPU_MONITOR_EXIT%"=="0" pause
exit /b %NPU_MONITOR_EXIT%
