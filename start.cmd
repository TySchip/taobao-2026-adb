@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
if exist "platform-tools\adb.exe" set "PATH=%~dp0platform-tools;%PATH%"
if exist "platform-tools\adb.exe" set "ADBUTILS_ADB_PATH=%~dp0platform-tools\adb.exe"
if not exist ".venv\Scripts\python.exe" (
    echo Please run install.cmd first.
    pause
    exit /b 1
)
:menu
echo.
echo 1. Preview current task list - no clicks
echo 2. Auto tasks - rescan after each task
echo 3. Upgrade racing car - consumes activity energy
echo 0. Exit
echo Connect your Android phone by USB and open the relevant Taobao page first.
echo Keep only the intended phone connected. Press Ctrl+C to stop.
choice /c 1230 /n /m "Select 1, 2, 3 or 0: "
if errorlevel 255 exit /b 1
if errorlevel 4 exit /b 0
if errorlevel 3 goto upgrade
if errorlevel 2 goto tasks
".venv\Scripts\python.exe" taobao_2026.py
if errorlevel 1 (
    echo Preview stopped. Resolve the reason above before starting tasks.
    goto menu
)
echo Preview finished. Select 2 once to run tasks with automatic rescanning.
goto menu
:tasks
".venv\Scripts\python.exe" taobao_2026.py --run
goto menu
:upgrade
".venv\Scripts\python.exe" taobao_2026.py --upgrade
goto menu
