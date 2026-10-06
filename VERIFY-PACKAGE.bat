@echo off
setlocal
cd /d "%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="
"%~dp0runtime\python\python.exe" -X utf8 -u "%~dp0tools\verify_portable.py"
set "T8_EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %T8_EXIT_CODE%
