@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Ploopy.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed. See the PowerShell error above.
  pause
  exit /b 1
)
echo.
echo Ploopy is installed.
pause
