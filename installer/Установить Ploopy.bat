@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Ploopy.ps1" -PackageRoot "%~dp0"
if errorlevel 1 (
  echo.
  echo Не удалось установить Ploopy. Проверь, что архив распакован полностью, и попробуй ещё раз.
  pause
  exit /b 1
)
echo.
echo Готово. Теперь Ploopy появится на рабочем столе.
pause
