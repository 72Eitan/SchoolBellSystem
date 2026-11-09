@echo off
title Bell System Launcher
rem Works from any folder: the project folder is the folder of this file.
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set APP_URL=http://localhost:8500/?main=1
rem Separate browser profile for the management window: STOP closes only this window, not the user's own browser.
set "APP_PROFILE=%APP_DIR%\.bell_window"
cd /d "%APP_DIR%"
call "%APP_DIR%\find_python.bat"
if not defined PY (
    echo ERROR: Python was not found. Run install.bat first.
    pause
    exit /b 1
)

echo ============================================
echo   Starting Bell System
echo   Folder: %APP_DIR%
echo   Python: %PY%
echo ============================================
echo.

echo [0/3] Stopping any copies that are still running...
"%PY%" src\stop_all.py
timeout /t 2 /nobreak >nul
echo.

rem Engine + management server run in the background with NO windows, under a watchdog
rem (src\watchdog.py) that restarts either of them within seconds if it stops unexpectedly.
rem A deliberate stop (STOP / closing the management window / stop_all.bat) stops the watchdog first.
rem Their output goes to logs\engine_console.log and logs\web_console.log.
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
echo [1/3] Starting playback engine + management server in the background ^(watchdog^)...
start "" "%PYW%" "%APP_DIR%\src\watchdog.py"
timeout /t 2 /nobreak >nul

echo [2/3] Waiting for the management server...
rem On a slow computer (or right after Windows starts) the server needs more than a few seconds.
rem Wait until it answers on port 8500 - up to 90 seconds - before opening the window.
echo       Waiting for the server to be ready ^(up to 90 seconds on a slow computer^)...
"%PY%" -c "import socket,time;any(socket.socket().connect_ex(('127.0.0.1',8500))==0 or time.sleep(1) for _ in range(90))"

rem Status reports + remote updates - only if this PC is connected to the control center.
rem Runs in the background with no window (pythonw). Separate from the engine: if it fails, the bells keep working.
rem stop_all does not stop it - it keeps reporting even when the bells are stopped. A second copy exits by itself.
if exist "%APP_DIR%\cloud_config.json" echo       Starting cloud reporter in the background - status and remote updates...
if exist "%APP_DIR%\cloud_config.json" start "" /min "%PYW%" "%APP_DIR%\src\cloud_reporter.py"

echo [3/3] Opening the management screen...
rem App window: Chrome or Edge (version 109 is the last on Windows 7 - fine). Otherwise Firefox / default browser.
set "BROWSER="
for %%B in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe") do (
    if not defined BROWSER if exist "%%~B" set "BROWSER=%%~B"
)
if defined BROWSER (
    start "" "%BROWSER%" --app=%APP_URL% --user-data-dir="%APP_PROFILE%" --no-first-run --no-default-browser-check --start-maximized --lang=he
) else (
    echo Chrome / Edge not found - opening in the default browser.
    echo NOTE: Internet Explorer cannot show the management screen. Use Chrome, Edge or Firefox,
    echo       or open the management screen from another computer: http://THIS-PC-IP:8500
    start "" "%APP_URL%"
)

echo.
echo ============================================
echo   The bell system is running in the background.
echo   Closing the management window (X) STOPS the bells -
echo   a warning is shown first. To start again: the
echo   "School Bell System" icon on the Desktop.
echo   Management from a phone / another computer
echo   never stops anything.
echo ============================================
timeout /t 5 >nul
