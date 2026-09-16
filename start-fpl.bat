@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Pulling the latest files from GitHub...
set "PULLLOG=%TEMP%\fpl-git-pull.txt"
git pull --quiet > "%PULLLOG%" 2>&1
if errorlevel 1 goto pullfailed
echo Updated.
goto python

:pullfailed
echo.
echo.
echo.
echo ============================================================
echo   UPDATE FAILED - this site will show older data
echo ============================================================
echo.
echo What git said:
echo ------------------------------------------------------------
type "%PULLLOG%"
echo ------------------------------------------------------------
echo.
findstr /I /C:"Could not resolve host" /C:"unable to access" /C:"Failed to connect" /C:"timed out" /C:"Network is unreachable" /C:"Could not fetch" /C:"remote end hung up" /C:"Connection refused" "%PULLLOG%" >nul
if not errorlevel 1 goto pullnet
findstr /I /C:"local changes" /C:"uncommitted" /C:"unstaged" /C:"would be overwritten" /C:"Please commit" /C:"stash them" /C:"stash before" "%PULLLOG%" >nul
if not errorlevel 1 goto pulldirty
findstr /I /C:"fast-forward" /C:"have diverged" /C:"Need to specify" /C:"CONFLICT" /C:"Could not apply" /C:"failed to merge" /C:"rebase in progress" "%PULLLOG%" >nul
if not errorlevel 1 goto pulldiverged
echo Likely cause: git could not update this folder.
goto pullmsg

:pullnet
echo Likely cause: no network.
goto pullmsg

:pulldirty
echo Likely cause: uncommitted local changes.
goto pullmsg

:pulldiverged
echo Likely cause: local branch and GitHub have diverged.
goto pullmsg

:pullmsg
echo.
type "%~dp0msg-pullfailed.txt"
echo.
echo ============================================================
echo.
pause
goto python

:python
echo Checking that Python is installed...
set "PY="
python -c "import sys" >nul 2>&1
if not errorlevel 1 goto usepython
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto usepy
echo Python is not on PATH.
echo Install Python from python.org and tick Add python.exe to PATH, then double-click this file again.
pause
exit /b 1

:usepython
set "PY=python"
goto ports

:usepy
set "PY=py -3"
goto ports

:ports
echo Finding a free port...
netstat -ano | findstr /C:":8000 " | findstr /I "LISTENING" >nul
if errorlevel 1 goto port8000
echo Port 8000 is already in use.
netstat -ano | findstr /C:":8001 " | findstr /I "LISTENING" >nul
if errorlevel 1 goto port8001
echo Port 8001 is already in use.
netstat -ano | findstr /C:":8002 " | findstr /I "LISTENING" >nul
if errorlevel 1 goto port8002
echo Port 8002 is already in use.
echo Ports 8000, 8001 and 8002 are already in use. Close the other program using them and try again.
pause
exit /b 1

:port8000
set "PORT=8000"
goto found
:port8001
set "PORT=8001"
goto found
:port8002
set "PORT=8002"
goto found

:found
echo Starting the site at http://localhost:%PORT%/
start "" cmd /c "ping -n 2 127.0.0.1 >nul & start http://localhost:%PORT%/"
echo Serving. Close this window to stop the server.
%PY% -m http.server %PORT% --directory "%~dp0web"
if errorlevel 1 goto serverfail
goto :eof

:serverfail
echo The local server stopped unexpectedly.
pause
exit /b 1
