@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_ON_WINDOWS.ps1"
set "OWG_EXIT=%ERRORLEVEL%"

if not "%OWG_EXIT%"=="0" (
  echo.
  echo OpenWorkGraph exited with code %OWG_EXIT%.
  echo See %%LOCALAPPDATA%%\OpenWorkGraph\logs\setup.log for setup details.
  pause
)

exit /b %OWG_EXIT%
