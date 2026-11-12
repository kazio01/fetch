@echo off
rem Fetch portable: index sample folders. Drop folders on this file, or run it to pick one.
rem Again on the same folder = only new and changed files. Ctrl+C stops; the next run continues.
setlocal
set "PY="
for /d %%D in ("%~dp0python\cpython-3.12*") do set "PY=%%D\python.exe"
if not exist "%PY%" (
  echo Run Setup.cmd first.
  pause
  exit /b 1
)
if not "%~1"=="" (
  "%PY%" "%~dp0app\fetch_portable.py" index %*
  goto :done
)
set "DIR="
for /f "usebackq delims=" %%F in (`powershell -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0app\pick_folder.ps1"`) do set "DIR=%%F"
if "%DIR%"=="" (
  echo No folder chosen. You can also type a path, e.g. \\nas\samples or Z:\Samples
  set /p "DIR=Folder: "
)
if "%DIR%"=="" exit /b 1
"%PY%" "%~dp0app\fetch_portable.py" index "%DIR%"
:done
echo.
pause
