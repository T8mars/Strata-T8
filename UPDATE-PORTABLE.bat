@echo off
setlocal
title Strata-T8 - Update
cd /d "%~dp0"
set PYTHONHOME=
set PYTHONPATH=
set PYTHONNOUSERSITE=1
set PYTHONDONTWRITEBYTECODE=1
"%~dp0runtime\python\python.exe" -X utf8 -u "%~dp0tools\portable_update.py" %*
set "T8_UPDATE_RC=%ERRORLEVEL%"
rem Parse this block before the updater can replace this batch file.
(
  if not "%T8_UPDATE_RC%"=="0" (
    pause
    exit /b 1
  )
  if "%~1"=="--check" (
    pause
    exit /b 0
  )
  if exist "%~dp0.portable-update\plan.json" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0.portable-update\apply.ps1" -PlanPath "%~dp0.portable-update\plan.json"
  if errorlevel 1 (
    pause
    exit /b 1
  )
  pause
  exit /b 0
)
