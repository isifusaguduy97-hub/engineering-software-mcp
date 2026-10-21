@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
if exist "%~dp0.venv\Scripts\python.exe" goto dependencies
py -3.12 --version >nul 2>&1
if not errorlevel 1 goto create_with_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 goto create_with_python
echo Python 3.12 or newer was not found. Install Python 3.12 from https://www.python.org/downloads/ and retry.
goto failed
:create_with_py
py -3.12 -m venv "%~dp0.venv"
if errorlevel 1 goto failed
goto dependencies
:create_with_python
python -m venv "%~dp0.venv"
if errorlevel 1 goto failed
:dependencies
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\setup.py" %*
if errorlevel 1 goto failed
echo Setup completed. Read codex-mcp.toml and the quick-start guide for client registration.
pause
exit /b 0
:failed
echo Setup failed. Copy the complete error above when requesting help.
pause
exit /b 1
