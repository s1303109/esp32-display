@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
py disable_autostart.py
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Could not disable the background bridge. Please take a screenshot of this window.
pause
exit /b 1
