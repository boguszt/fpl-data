@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo Pulling the latest files from GitHub...
set "PULLLOG=%TEMP%\fpl-git-pull.txt"
set "OLDHEAD="
set "N=0"
for /f %%H in ('git rev-parse HEAD 2^>nul') do set "OLDHEAD=%%H"

git pull --ff-only > "%PULLLOG%" 2>&1
if errorlevel 1 goto pullfailed
if not defined OLDHEAD goto python
for /f %%C in ('git rev-list --count !OLDHEAD!..HEAD') do set "N=%%C"
if not "!N!"=="0" echo Pulled !N! new commits from GitHub.
goto python

:pullfailed
echo.
echo.
echo.
echo ============================================================
echo   UPDATE FAILED - this site will show older data
echo ============================================================
echo.
echo Git could not download the latest files.
echo The usual reason is uncommitted edits in this folder.
echo Cursor often leaves some. Git will not overwrite those.
echo.
echo What git said:
echo ------------------------------------------------------------
type "%PULLLOG%"
echo ------------------------------------------------------------
echo.
echo What to do: commit or stash those edits, then double-click
echo this file again. The site will still open with whatever is
echo already on this computer.
echo.
echo ============================================================
echo.

:python
echo Checking that Python is installed...
set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto usepy
python -c "import sys" >nul 2>&1
if not errorlevel 1 goto usepython
echo Python is not on PATH.
echo Install Python from python.org and tick Add python.exe to PATH, then double-click this file again.
pause
exit /b 1

:usepy
set "PY=py -3"
goto ports
:usepython
set "PY=python"

:ports
echo Finding a free port...
set "PORT="
netstat -ano | findstr /C:":8000 " | findstr /I "LISTENING" >nul
if errorlevel 1 set "PORT=8000"
if defined PORT goto found
echo Port 8000 is already in use.

netstat -ano | findstr /C:":8001 " | findstr /I "LISTENING" >nul
if errorlevel 1 set "PORT=8001"
if defined PORT goto found
echo Port 8001 is already in use.

netstat -ano | findstr /C:":8002 " | findstr /I "LISTENING" >nul
if errorlevel 1 set "PORT=8002"
if defined PORT goto found
echo Port 8002 is already in use.

echo Ports 8000, 8001 and 8002 are already in use. Close the other program using them and try again.
pause
exit /b 1

:found
echo Starting the site at http://localhost:!PORT!/
start "" cmd /c "ping -n 2 127.0.0.1 >nul & start http://localhost:!PORT!/"
echo Serving. Close this window to stop the server.
!PY! -m http.server !PORT! --directory "%~dp0web"
if errorlevel 1 goto serverfail
goto :eof

:serverfail
echo The local server stopped unexpectedly.
pause
exit /b 1
