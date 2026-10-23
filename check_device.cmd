@echo off
setlocal
cd /d "%~dp0"
if exist "platform-tools\adb.exe" set "PATH=%~dp0platform-tools;%PATH%"
if exist "platform-tools\adb.exe" set "ADBUTILS_ADB_PATH=%~dp0platform-tools\adb.exe"
if not exist ".venv\Scripts\python.exe" (
    echo Please run install.cmd first.
    pause
    exit /b 1
)
echo The phone should appear with status device.
echo unauthorized: unlock the phone and allow USB debugging.
echo offline or empty: check the USB cable, driver and USB debugging switch.
".venv\Scripts\python.exe" -c "import subprocess; from adbutils import adb_path; raise SystemExit(subprocess.call([adb_path(), 'devices', '-l']))"
pause
