@echo off
title Bell System - Backup to Git + GitHub
rem Backup: commit all changes to the local Git repository and push to GitHub.
rem Asks for a description (Hebrew is fine - paste the ready description Claude gives after each update).
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
call "%APP_DIR%\find_python.bat"
if not defined PY (
    echo ERROR: Python was not found.
    pause
    exit /b 1
)
"%PY%" "%APP_DIR%\tools\backup_git.py"
