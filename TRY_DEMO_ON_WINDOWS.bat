@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_ON_WINDOWS.ps1" -Mode demo
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo If your organization blocks PowerShell scripts, use the signed OpenWorkGraph installer
  echo when available or ask IT to allow the OpenWorkGraph launcher.
  echo Setup log: %%LOCALAPPDATA%%\OpenWorkGraph-logs\setup.log
  pause
)
exit /b %EXIT_CODE%
