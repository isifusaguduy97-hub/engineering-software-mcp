@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" goto missing
if not exist "%~dp0config.local.json" goto missing
"%~dp0.venv\Scripts\python.exe" -m engineering_mcp.server --config "%~dp0config.local.json" %*
exit /b %errorlevel%
:missing
echo Run Setup.cmd first. 1>&2
exit /b 1
