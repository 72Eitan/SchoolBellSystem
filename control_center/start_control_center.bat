@echo off
title Et Ratzon - Control Center
rem Starts the control center (customer dashboard) on port 8600 and opens it in the browser.
rem First run: the page asks you to create the owner account.
rem Demo data instead:  start_control_center.bat demo
rem If setup_tunnel.bat was run, the public address (data\public_url.txt) is used automatically.
set "CC_DIR=%~dp0"
if "%CC_DIR:~-1%"=="\" set "CC_DIR=%CC_DIR:~0,-1%"
cd /d "%CC_DIR%"
call "%CC_DIR%\..\find_python.bat"
if not defined PY (
    echo ERROR: Python was not found. Run install.bat first.
    pause
    exit /b 1
)
"%PY%" -c "import flask" 2>nul || "%PY%" -m pip install flask
"%PY%" -c "import waitress" 2>nul || "%PY%" -m pip install waitress
set "ARGS="
if /i "%~1"=="demo" set "ARGS=--demo"
if /i "%~1"=="simulate" set "ARGS=--simulate"
set "PUBURL="
if exist "%CC_DIR%\data\public_url.txt" set /p PUBURL=<"%CC_DIR%\data\public_url.txt"
if defined PUBURL (
    echo Public address for schools: %PUBURL%
    set "ARGS=%ARGS% --host 127.0.0.1 --public-url %PUBURL%"
)
start "" cmd /c "timeout /t 4 /nobreak >nul && start http://localhost:8600"
"%PY%" server.py %ARGS%
pause
