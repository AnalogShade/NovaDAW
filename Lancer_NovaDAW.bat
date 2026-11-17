@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
) else (
    where pythonw >nul 2>&1
    if %errorlevel%==0 (
        start "" pythonw "%~dp0main.py"
    ) else (
        python "%~dp0main.py"
    )
)
exit /b 0
