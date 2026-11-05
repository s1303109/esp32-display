@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo Installing the ESP32 Music Display background bridge...
py install_autostart.py
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Setup failed. Please take a screenshot of this window.
pause
exit /b 1
