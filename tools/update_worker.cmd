@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%T8_UPDATE_APP%\tools\run_portable_update.ps1" -AppRoot "%T8_UPDATE_APP%"
set "T8_WORKER_RC=%ERRORLEVEL%"
if not "%T8_WORKER_RC%"=="0" goto failed
exit /b 0
:failed
pause
exit /b %T8_WORKER_RC%
