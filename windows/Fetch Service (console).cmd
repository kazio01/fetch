@echo off
rem Fetch portable: run the search service in this window (for troubleshooting).
rem Normally the VST3 plugin starts it by itself, without a window (log: data\service.log).
setlocal
set "PY="
for /d %%D in ("%~dp0python\cpython-3.12*") do set "PY=%%D\python.exe"
if not exist "%PY%" (
  echo Run Setup.cmd first.
  pause
  exit /b 1
)
echo Fetch search service on http://127.0.0.1:47811 - close this window to stop it.
"%PY%" "%~dp0app\fetch_portable.py" serve
pause
