@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
  start "" pythonw "%~dp0launcher.py"
  exit /b
)
where pyw >nul 2>nul
if %errorlevel% equ 0 (
  start "" pyw -3 "%~dp0launcher.py"
  exit /b
)
echo Python 3.10+ is required. Install Python from python.org.
pause
