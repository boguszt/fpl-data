@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Pulling the latest files from GitHub...
git pull --quiet --ff-only
if errorlevel 1 echo Git pull failed. Opening the copy already on this computer.

echo Checking that Python is installed...
set "PY="
python -c "import sys" >nul 2>&1
if not errorlevel 1 (
  set "PY=python"
) else (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  echo Python is not on PATH.
  echo Install Python from python.org and tick "Add python.exe to PATH", then double-click this file again.
  pause
  exit /b 1
)

echo Finding a free port...
set "PORT="
for %%P in (8000 8001 8002) do (
  netstat -ano | findstr /C:":%%P " | findstr /I "LISTENING" >nul
  if errorlevel 1 (
    set "PORT=%%P"
    goto :found
  )
  echo Port %%P is already in use.
)
echo Ports 8000, 8001 and 8002 are already in use. Close the other program using them and try again.
pause
exit /b 1

:found
echo Starting the site at http://localhost:%PORT%/
start "" cmd /c "ping -n 2 127.0.0.1 >nul & start http://localhost:%PORT%/"
echo Serving. Close this window to stop the server.
%PY% -m http.server %PORT% --directory web
if errorlevel 1 (
  echo The local server stopped unexpectedly.
  pause
  exit /b 1
)
