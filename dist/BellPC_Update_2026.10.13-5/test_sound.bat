@echo off
title Bell System - Sound test
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
call "%APP_DIR%\find_python.bat"
if not defined PY (
    echo Python was not found. Run install.bat first.
    pause
    exit /b 1
)
"%PY%" src\test_sound.py
echo.
pause
