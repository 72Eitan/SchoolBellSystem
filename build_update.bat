@echo off
title Et Ratzon - Build signed update
rem Builds a signed update package for all bell PCs (run on the developer PC only).
rem First time only:   build_update.bat keygen
rem Every update:      change SERVER_VERSION in src\web_server.py, then run build_update.bat
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
call "%APP_DIR%\find_python.bat"
if not defined PY (
    echo ERROR: Python was not found. Run install.bat first.
    pause
    exit /b 1
)
"%PY%" tools\build_update.py %*
echo.
pause
