$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Dist = Join-Path $Root "dist"
$PackageName = "OpenWorkGraph-Windows-v$Version"
$Package = Join-Path $Dist $PackageName
$Payload = Join-Path $Package ".openworkgraph-src"

if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Payload | Out-Null

$robocopyArgs = @(
    $Root,
    $Payload,
    "/MIR",
    "/R:2",
    "/W:1",
    "/NFL",
    "/NDL",
    "/NJH",
    "/NJS",
    "/NP",
    "/XD", ".git", ".github", ".venv", ".runtime", ".pytest_cache", "__pycache__", "data", "dist", "tests", "scripts",
    "/XF", "config.json"
)
& robocopy @robocopyArgs | Out-Host
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

@'
@echo off
setlocal
call "%~dp0.openworkgraph-src\START_ON_WINDOWS.bat"
exit /b %ERRORLEVEL%
'@ | Set-Content -Path (Join-Path $Package "START_OPENWORKGRAPH.cmd") -Encoding ascii

Copy-Item (Join-Path $Root "ADD_BROWSER_SENSOR_WINDOWS.bat") (Join-Path $Package "ADD_BROWSER_SENSOR.cmd")

@"
OpenWorkGraph $Version - Windows tester build
==============================================

1. Unzip this folder.
2. Double-click START_OPENWORKGRAPH.cmd.
3. Windows may show a security prompt because this is an early unsigned prototype;
   review the files/source and choose Run only if you trust this repository.
4. On first launch, OpenWorkGraph downloads its own private runtime and installs
   itself under %LOCALAPPDATA%\OpenWorkGraph. No system Python is required.
5. The local dashboard opens automatically at http://127.0.0.1:8787.

Browser context (recommended)
-----------------------------
After OpenWorkGraph has started once, double-click ADD_BROWSER_SENSOR.cmd.
Explorer opens the browser_extension folder and Chrome/Edge opens its Extensions
page. Enable Developer mode, choose Load unpacked, and select the folder Explorer
opened. This lets OpenWorkGraph distinguish Gmail, Docs, Salesforce and other
browser work instead of seeing only the browser application.

Windows native UI semantics
---------------------------
OpenWorkGraph uses Microsoft UI Automation on a best-effort basis to identify
native controls such as buttons and menu items. It records safe control metadata
(role/name/automation ID/class), not typed field values, selected text or password
values. Applications running at a higher privilege level or without a UIA provider
may expose less semantic detail.

Privacy / data location
-----------------------
The prototype runs locally. Captured data stays on this computer unless you
explicitly export it. Aggregate keyboard activity is counted, but typed text and
key identities are not recorded by the effort counter.

Stop OpenWorkGraph with Ctrl+C in the console window it opened.

Project: https://github.com/KAVentures/openworkgraph
"@ | Set-Content -Path (Join-Path $Package "README_FIRST.txt") -Encoding utf8

$Zip = Join-Path $Dist "OpenWorkGraph-Windows.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Push-Location $Dist
try {
    # Windows includes bsdtar as tar.exe. Using it preserves dot-prefixed payload
    # folders reliably while keeping the ZIP structure simple.
    & tar.exe -a -c -f "OpenWorkGraph-Windows.zip" $PackageName
    if ($LASTEXITCODE -ne 0) { throw "tar.exe failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$Hash = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLowerInvariant()
"$Hash  OpenWorkGraph-Windows.zip" | Set-Content -Path "$Zip.sha256" -Encoding ascii
Write-Host "Built: $Zip"
