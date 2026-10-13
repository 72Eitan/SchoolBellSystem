@echo off
rem ============================================================
rem  find_python.bat - sets PY to the full path of python.exe
rem  Used by install.bat, start_all.bat and stop_all.bat.
rem  Order: Python 3.8 (the last version that runs on Windows 7),
rem  then any python on PATH.
rem ============================================================
set "PY="
for %%P in ("%LocalAppData%\Programs\Python\Python38\python.exe" "%LocalAppData%\Programs\Python\Python38-32\python.exe" "%ProgramFiles%\Python38\python.exe" "%ProgramFiles(x86)%\Python38-32\python.exe" "C:\Python38\python.exe" "C:\Python38-32\python.exe") do (
    if not defined PY if exist "%%~P" set "PY=%%~P"
)
if not defined PY (
    for /f "delims=" %%E in ('py -3.8 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%E"
)
if not defined PY (
    for /f "delims=" %%E in ('where python 2^>nul') do if not defined PY set "PY=%%E"
)
