@echo off
title Bell System - Stop
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
call "%APP_DIR%\find_python.bat"
echo ============================================
echo   Stopping Bell System (engine + server)
echo   Bells will NOT ring until start_all.bat runs again.
echo ============================================
echo.
"%PY%" src\stop_all.py
echo.
echo Done.
timeout /t 6 >nul
