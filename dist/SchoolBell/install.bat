@echo off
title Bell System - Install
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
echo ============================================
echo   Bell System - installation
echo   Folder: %APP_DIR%
echo ============================================
echo.

call "%APP_DIR%\find_python.bat"
if not defined PY (
    echo ERROR: Python 3.8 is not installed.
    echo   1. Install python-3.8.10-amd64.exe ^(64-bit Windows^) or python-3.8.10.exe ^(32-bit^)
    echo      and tick "Add Python 3.8 to PATH" on the first screen.
    echo   2. Run install.bat again.
    echo See the installation guide ^(Hebrew^) in this folder.
    pause
    exit /b 1
)
echo [1/3] Python found: %PY%
"%PY%" --version
echo.

echo [2/3] Installing libraries from the "wheels" folder ^(no internet needed^)...
"%PY%" -m pip install --disable-pip-version-check --no-index --find-links "%APP_DIR%\wheels" -r "%APP_DIR%\requirements.txt"
if errorlevel 1 (
    echo.
    echo Offline install did not work ^(probably not Python 3.8^). Trying from the internet...
    "%PY%" -m pip install --disable-pip-version-check -r "%APP_DIR%\requirements.txt"
)
"%PY%" -c "import pygame, flask, openpyxl; print('Libraries OK')" 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: the libraries could not be installed. Please send a photo of this window.
    pause
    exit /b 1
)
echo.

echo Turning off sleep mode ^(a sleeping computer does not ring^)...
powercfg -change -standby-timeout-ac 0 >nul 2>&1
powercfg -change -hibernate-timeout-ac 0 >nul 2>&1
echo.

echo [3/3] Creating shortcuts: Desktop + start automatically when Windows starts...
set "VBS=%TEMP%\bell_shortcuts.vbs"
rem The "Et Ratzon" clock icon; if the file is missing, the standard Windows icon is used
set "ICON=%SystemRoot%\System32\SHELL32.dll,137"
if exist "%APP_DIR%\app_icon.ico" set "ICON=%APP_DIR%\app_icon.ico,0"
> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>>"%VBS%" echo Sub MakeLink(path, target, icon)
>>"%VBS%" echo   Set l = sh.CreateShortcut(path)
>>"%VBS%" echo   l.TargetPath = target
>>"%VBS%" echo   l.WorkingDirectory = "%APP_DIR%"
>>"%VBS%" echo   l.IconLocation = icon
>>"%VBS%" echo   l.Save
>>"%VBS%" echo End Sub
>>"%VBS%" echo d = sh.SpecialFolders("Desktop")
>>"%VBS%" echo s = sh.SpecialFolders("Startup")
>>"%VBS%" echo MakeLink d ^& "\School Bell System.lnk", "%APP_DIR%\start_all.bat", "%ICON%"
>>"%VBS%" echo MakeLink d ^& "\School Bell System - STOP.lnk", "%APP_DIR%\stop_all.bat", "%SystemRoot%\System32\SHELL32.dll,131"
>>"%VBS%" echo MakeLink s ^& "\School Bell System.lnk", "%APP_DIR%\start_all.bat", "%ICON%"
cscript //nologo "%VBS%"
del "%VBS%" >nul 2>&1
echo Shortcuts created.
echo.
echo ============================================
echo   Installation finished.
echo   Starting the system now...
echo ============================================
timeout /t 3 >nul
call "%APP_DIR%\start_all.bat"
