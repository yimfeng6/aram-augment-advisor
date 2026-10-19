@echo off
chcp 65001 >nul
title Hextech Augment Advisor - Web
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

set "PY=D:\anaconda\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================
echo   Hextech Augment Advisor  (web UI)
echo ============================================
echo   Browser: http://127.0.0.1:8000
echo   Press Ctrl+C to stop.
echo.

"%PY%" web.py --open %*

echo.
echo Stopped.
pause
