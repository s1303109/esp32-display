@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_music_display.ps1" -Mode Disable
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Could not disable the background bridge. Please take a screenshot of this window.
pause
exit /b 1
