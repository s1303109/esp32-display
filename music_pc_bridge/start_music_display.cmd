@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo Preparing the Windows music bridge...
py -m pip install --user -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Connecting to the ESP32 music display. Keep this window open.
py bridge.py
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo The music bridge could not start. Please take a screenshot of this window.
pause
exit /b 1
