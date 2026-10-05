@echo off
setlocal
title Strata-T8 - Update
set "T8_UPDATE_APP=%~dp0."
set "T8_UPDATE_OPTIONS=%*"
:pick_worker
set "T8_UPDATE_WORKER=%TEMP%\Strata-T8-update-worker-%RANDOM%-%RANDOM%.cmd"
if exist "%T8_UPDATE_WORKER%" goto pick_worker
copy "%~dp0tools\update_worker.cmd" "%T8_UPDATE_WORKER%" >nul
if errorlevel 1 goto failed
rem Chain without CALL: the active batch file becomes this temporary worker.
"%T8_UPDATE_WORKER%"
:failed
echo Could not prepare the temporary update worker.
pause
exit /b 1
