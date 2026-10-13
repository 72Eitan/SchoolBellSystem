@echo off
title Bell System - Cloud Reporter
rem Sends a status report (heartbeat) to the Et Ratzon control center every minute.
rem Separate from the bell engine: if it fails or there is no internet, the bells keep working.
rem Needs cloud_config.json in this folder (downloaded from the control center).
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
if not exist "%APP_DIR%\cloud_config.json" (
    echo cloud_config.json was not found - nothing to report to.
    echo Create the customer in the control center and save its cloud_config.json here.
    pause
    exit /b 1
)
call "%APP_DIR%\find_python.bat"
if not defined PY (
    echo ERROR: Python was not found. Run install.bat first.
    pause
    exit /b 1
)
echo Testing one report...
"%PY%" src\cloud_reporter.py --once
echo.
echo Starting the reporter - minimized window.
start "Bell Cloud Reporter - cloud_reporter.py" /min cmd /k "cd /d "%APP_DIR%" && "%PY%" src\cloud_reporter.py"
timeout /t 5 >nul
