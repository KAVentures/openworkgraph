@echo off
setlocal
cd /d "%~dp0"

set "OWG_MODE_ARGS="
if /I "%OWG_INSTALL_MODE%"=="demo" set "OWG_MODE_ARGS=-Mode demo"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_ON_WINDOWS.ps1" %OWG_MODE_ARGS%
set "OWG_EXIT=%ERRORLEVEL%"

if not "%OWG_EXIT%"=="0" (
  echo.
  echo OpenWorkGraph exited with code %OWG_EXIT%.
  echo See %%LOCALAPPDATA%%\OpenWorkGraph-logs\setup.log for setup details.
  echo.
  echo If your organization blocks PowerShell scripts, use the signed OpenWorkGraph
  echo installer when available or ask IT to allow the OpenWorkGraph launcher.
  echo The tester ZIP cannot bypass an organization-enforced execution policy.
  pause
)

exit /b %OWG_EXIT%
