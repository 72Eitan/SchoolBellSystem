@echo off
rem STOP shortcut ("School Bell System - STOP"): asks first, in a Windows dialog shown on top of the
rem management window (which stays on screen): "Closing the system will disable the bells. Continue?"
rem   Yes -> everything stops (engine, server, management window).   No -> nothing happens.
rem To stop immediately without asking (for technicians):  stop_all.bat /force
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
call "%APP_DIR%\find_python.bat"
if /i "%~1"=="/force" goto force
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
start "" "%PYW%" "%APP_DIR%\src\close_confirm.py" --keep-window
exit /b 0

:force
title Bell System - Stop
echo Stopping Bell System (engine + server)...
"%PY%" src\stop_all.py
echo Done.
timeout /t 4 >nul
