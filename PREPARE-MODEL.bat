@echo off
setlocal
title Strata-T8 - Prepare Model
cd /d "%~dp0"
set PYTHONHOME=
set PYTHONPATH=
set PYTHONNOUSERSITE=1
set PYTHONDONTWRITEBYTECODE=1
"%~dp0runtime\python\python.exe" -X utf8 -u "%~dp0tools\prepare_portable_model.py" %*
set "T8_EXIT_CODE=%ERRORLEVEL%"
if not "%T8_EXIT_CODE%"=="0" pause
exit /b %T8_EXIT_CODE%
