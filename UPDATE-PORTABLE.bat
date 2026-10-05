@echo off
setlocal
title Strata-T8 - Update
cd /d "%~dp0"
set "T8_UPDATE_OPTIONS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run_portable_update.ps1" -AppRoot "%~dp0."
exit /b %ERRORLEVEL%
