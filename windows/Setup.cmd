@echo off
rem Fetch portable: install Python, packages, ffmpeg and the CLAP model into this folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\setup.ps1" %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" echo. & echo Setup failed (%RC%). See the messages above.
if /i not "%~1"=="-Quiet" pause
exit /b %RC%
