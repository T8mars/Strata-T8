@echo off
setlocal
cd /d "%~dp0"
"%~dp0runtime\python\python.exe" -X utf8 -u "%~dp0tools\verify_portable.py"
pause
