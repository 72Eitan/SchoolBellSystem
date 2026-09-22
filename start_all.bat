@echo off
title Bell System Launcher

set PROJECT_DIR=D:\Projects\SchoolBellSystem
cd /d "%PROJECT_DIR%"

echo ============================================
echo   Starting Bell System
echo   Project folder: %PROJECT_DIR%
echo ============================================
echo.

echo [1/2] Starting playback engine (main.py)...
start "Bell Engine - main.py" cmd /k "cd /d %PROJECT_DIR% && python src\main.py"

timeout /t 2 /nobreak >nul

echo [2/2] Starting management server (web_server.py)...
start "Bell Web Server - web_server.py" cmd /k "cd /d %PROJECT_DIR% && python src\web_server.py"

timeout /t 2 /nobreak >nul

echo Opening management interface in browser...
start "" "http://localhost:8500"

echo.
echo ============================================
echo   Both processes are now running in separate windows.
echo   Do NOT close those cmd windows - they must
echo   stay open while the system is active.
echo ============================================
echo.
echo You may close this window now.
pause >nul
