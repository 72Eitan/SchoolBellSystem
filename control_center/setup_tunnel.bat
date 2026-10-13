@echo off
title Et Ratzon - Public address (Tailscale Funnel)
rem One-time setup: gives the control center a fixed public HTTPS address through Tailscale Funnel.
rem Before running: install Tailscale (https://tailscale.com/download/windows) and sign in.
rem Run again any time to see the status:  setup_tunnel.bat status
set "CC_DIR=%~dp0"
if "%CC_DIR:~-1%"=="\" set "CC_DIR=%CC_DIR:~0,-1%"
cd /d "%CC_DIR%"
call "%CC_DIR%\..\find_python.bat"
if not defined PY (
    echo ERROR: Python was not found. Run install.bat first.
    pause
    exit /b 1
)
set "CMD=%~1"
if not defined CMD set "CMD=setup"
"%PY%" tunnel.py %CMD%
echo.
pause
