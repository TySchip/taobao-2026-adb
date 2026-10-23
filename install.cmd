@echo off
setlocal
cd /d "%~dp0"
echo This creates .venv in this folder and installs requirements.txt.
echo No global Python packages or system settings will be changed.
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Done. Read README.md, connect Android, then open start.cmd.
pause
exit /b 0
:failed
echo Installation failed. Check the error above. Python 3.10+ is recommended.
pause
exit /b 1
