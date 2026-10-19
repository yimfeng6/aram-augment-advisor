@echo off
chcp 65001 >nul
title Hextech Augment Advisor
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

set "PY=D:\anaconda\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================
echo   Hextech Augment Advisor  (game overlay)
echo ============================================
echo   Keep this window open while playing.
echo   Press Ctrl+C to stop.
echo.

"%PY%" watch.py %*

echo.
echo Stopped.
pause
