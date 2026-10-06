@echo off
setlocal

set "ROOT=%~dp0"
set "INSTALL_DIR=%LOCALAPPDATA%\OpenWorkGraph"
set "SENSOR_DIR="

if exist "%INSTALL_DIR%\browser_extension\manifest.json" set "SENSOR_DIR=%INSTALL_DIR%\browser_extension"
if not defined SENSOR_DIR if exist "%ROOT%.openworkgraph-src\browser_extension\manifest.json" set "SENSOR_DIR=%ROOT%.openworkgraph-src\browser_extension"
if not defined SENSOR_DIR if exist "%ROOT%browser_extension\manifest.json" set "SENSOR_DIR=%ROOT%browser_extension"

if not defined SENSOR_DIR (
  echo.
  echo Could not find the OpenWorkGraph browser sensor folder.
  echo Start OpenWorkGraph once, then run this helper again.
  echo.
  pause
  exit /b 1
)

echo.
echo OpenWorkGraph browser sensor
echo ============================
echo.
echo Explorer will open the browser_extension folder.
echo In Chrome or Edge:
echo   1. Open the Extensions page.
echo   2. Turn on Developer mode.
echo   3. Click Load unpacked.
echo   4. Select the browser_extension folder Explorer just opened.
echo.
echo This lets OpenWorkGraph distinguish Gmail, Docs, Salesforce and other
 echo browser work instead of seeing only the browser application.
echo.

start "" explorer.exe "%SENSOR_DIR%"

set "CHROME="
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if defined ProgramFiles(x86) if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"

if defined CHROME (
  start "" "%CHROME%" "chrome://extensions/"
  goto opened
)

set "EDGE="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined EDGE if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if defined EDGE start "" "%EDGE%" "edge://extensions/"

:opened
echo Folder: %SENSOR_DIR%
echo.
pause
