@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "ENGINEERING_MCP_CONFIG=%~dp0config.local.json"
if not exist "%~dp0.venv\Scripts\python.exe" goto missing
if not exist "%~dp0config.local.json" goto missing
if not exist "%~dp0bridges\labview\labview_bindings.py" goto bindings
echo Starting the local LabVIEW connector. Keep this window open while using LabVIEW through MCP.
"%~dp0.venv\Scripts\python.exe" "%~dp0bridges\labview\labview_host.py" --config "%~dp0config.local.json"
set "CONNECTOR_EXIT=%errorlevel%"
echo.
echo LabVIEW connector stopped. Copy the full error above if one was reported.
pause
exit /b %CONNECTOR_EXIT%
:missing
echo Run Setup.cmd first.
pause
exit /b 1
:bindings
echo Run .venv\Scripts\python.exe -m pip install pywin32==312
echo Then run .venv\Scripts\python.exe tools\prepare_labview.py
pause
exit /b 1
