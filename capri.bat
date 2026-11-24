@echo off
setlocal
cd /d "%~dp0"
title Capri
echo Starting Capri...

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [!] Capri isn't set up yet: the .venv folder is missing.
    echo     In this folder, right-click empty space ^> "Open in Terminal", then run:
    echo         powershell -ExecutionPolicy Bypass -File setup.ps1
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -u -m capri %*
if errorlevel 1 (
    echo.
    echo [!] Capri stopped with an error. The details are above and in logs\crash.log.
    echo     Tip: run  capri.bat doctor  to check every part.
    echo.
    pause
)
