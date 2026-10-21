@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" goto missing
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\check.py" %*
set "CHECK_EXIT=%errorlevel%"
pause
exit /b %CHECK_EXIT%
:missing
echo Run Setup.cmd first.
pause
exit /b 1
