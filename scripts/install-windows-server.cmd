@echo off
rem ===========================================================================================
rem  Product Costing - install or upgrade on Windows Server (without Docker)
rem
rem  Needs:  Python 3.13 (python.org, with the "py" launcher)  and  PostgreSQL (EDB installer)
rem  Use:    right-click this file > "Run as administrator"
rem ===========================================================================================
setlocal
cd /d "%~dp0.."
set "ROOT=%cd%"
set PYTHONUTF8=1

net session >nul 2>&1
if errorlevel 1 (
  echo Please right-click install-windows-server.cmd and choose "Run as administrator".
  goto :fail
)

set "PY="
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"
if not defined PY py -3.14 -c "import sys" >nul 2>&1 && set "PY=py -3.14"
if not defined PY (
  echo Python 3.13 was not found. Install it from https://www.python.org/downloads/windows/
  echo ^(Windows installer 64-bit, "Customize installation", tick "Install Python for all users"^),
  echo then run this file again.
  goto :fail
)

if not exist "%ROOT%\frontend\dist\index.html" (
  echo The web interface is missing. Use the Windows package productcosting-windows.zip,
  echo not the source-code download - or build it with Node.js: cd frontend, npm ci, npm run build
  goto :fail
)

if not exist "%ROOT%\venv\Scripts\python.exe" (
  echo Creating the Python environment ...
  %PY% -m venv "%ROOT%\venv" || goto :fail
)
echo Installing the application's Python packages - this takes a few minutes ...
"%ROOT%\venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pip || goto :fail
"%ROOT%\venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r "%ROOT%\backend\requirements.txt" || goto :fail

cd /d "%ROOT%\backend"
"%ROOT%\venv\Scripts\python.exe" -m app.cli setup-windows --root "%ROOT%" || goto :fail
echo.
pause
exit /b 0

:fail
echo.
echo The installation stopped - see the messages above.
pause
exit /b 1
