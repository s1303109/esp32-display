@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "PYTHON_EXE="
if exist "%LOCALAPPDATA%\ESP32MusicDisplay\python_path.txt" for /f "usebackq delims=" %%P in ("%LOCALAPPDATA%\ESP32MusicDisplay\python_path.txt") do set "PYTHON_EXE=%%P"
if defined PYTHON_EXE if exist "%PYTHON_EXE%" goto run_python
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if exist "%PYTHON_EXE%" goto run_python
where py >nul 2>nul
if errorlevel 1 goto missing_python
py -3.14 "%~dp0flash_firmware.py"
goto finished

:run_python
"%PYTHON_EXE%" "%~dp0flash_firmware.py"
goto finished

:missing_python
echo Python 3.14 was not found. Install the Windows bridge first.
set "FLASH_RESULT=1"
goto show_result

:finished
set "FLASH_RESULT=%ERRORLEVEL%"
:show_result
echo.
if not "%FLASH_RESULT%"=="0" echo Flashing did not complete. Review the message above.
pause
exit /b %FLASH_RESULT%
