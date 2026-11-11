@echo off
rem Mana Ledger on this PC: double-click to start. Closing this window stops the app.
setlocal
cd /d "%~dp0"
title Mana Ledger
set "URL=http://127.0.0.1:8000/"

rem Already running? Just open it.
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul 2>nul && (
  echo Mana Ledger is already running. Opening it...
  start "" "%URL%"
  exit /b 0
)

rem ---- Find Python 3.10 or newer --------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul || goto nopython

rem ---- First run: a private Python setup in the .venv folder -----------------
set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" goto venv_ok
echo Setting up Mana Ledger for the first time. This takes a minute or two...
%PY% -m venv .venv || goto fail
:venv_ok

rem Install packages when they're missing or requirements.txt changed.
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul && goto packages_ok
echo Installing the packages Mana Ledger needs...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt || goto fail
copy /y requirements.txt ".venv\requirements.installed" >nul
:packages_ok

echo Updating the database...
"%VPY%" manage.py migrate --noinput -v0 || goto fail

rem ---- First run: bring your data over from a backup file ---------------------
if exist ".data-loaded" goto start
set "BACKUP="
for /f "delims=" %%F in ('dir /b /o-d "%~dp0manaledger-backup-*.json" 2^>nul') do if not defined BACKUP set "BACKUP=%~dp0%%F"
if not defined BACKUP for /f "delims=" %%F in ('dir /b /o-d "%USERPROFILE%\Downloads\manaledger-backup-*.json" 2^>nul') do if not defined BACKUP set "BACKUP=%USERPROFILE%\Downloads\%%F"
if not defined BACKUP goto nobackup
echo Loading your cards and decks from:
echo   %BACKUP%
"%VPY%" manage.py loaddata "%BACKUP%" || goto fail
echo %BACKUP%> ".data-loaded"
goto start

:nobackup
echo.
echo No backup file found, so Mana Ledger starts empty.
echo To bring your cards over: in your Codespace's Mana Ledger, open
echo Backup ^& database and press Download backup, then close this
echo window and start Mana Ledger again. (Or restore it on the same page here.)
echo.
echo none> ".data-loaded"

:start
echo.
echo Mana Ledger is starting at %URL%
echo Keep this window open while you use it. Close it to stop the app.
echo.
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000/"
"%VPY%" manage.py runserver 127.0.0.1:8000
goto end

:nopython
echo.
echo Mana Ledger needs Python 3.10 or newer, and it isn't installed.
echo Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),
echo or run this in PowerShell:   winget install -e --id Python.Python.3.12
echo Then double-click "Start Mana Ledger" again.
echo.
pause
exit /b 1

:fail
echo.
echo Something went wrong (see the message above). Nothing in your data was changed.
pause
exit /b 1

:end
endlocal
