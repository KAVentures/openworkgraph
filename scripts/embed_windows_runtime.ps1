$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Payload = Join-Path $Root "dist\OpenWorkGraph-Windows-v$Version\.openworkgraph-src"

if (-not (Test-Path $Payload)) {
    throw "Expected packaged payload at $Payload. Run scripts/build_windows_release.ps1 first."
}

$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv) {
    throw "uv is required at build time. In GitHub Actions use astral-sh/setup-uv before this script."
}

$Venv = Join-Path $Payload ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Pythonw = Join-Path $Venv "Scripts\pythonw.exe"

if (Test-Path $Venv) {
    Remove-Item $Venv -Recurse -Force
}

Push-Location $Payload
try {
    & $Uv venv --python 3.12 --managed-python ".venv"
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed with exit code $LASTEXITCODE" }

    & $Uv pip install --python $Python "."
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed with exit code $LASTEXITCODE" }

    & $Python -c "import fastapi, mcp, pystray, win32com.client, uiautomation; import server.secure_app, collector.main"
    if ($LASTEXITCODE -ne 0) { throw "embedded runtime import smoke test failed" }
} finally {
    Pop-Location
}

if (-not (Test-Path $Pythonw)) {
    throw "Embedded pythonw.exe was not created: $Pythonw"
}

Set-Content -Path (Join-Path $Payload "OFFLINE_RUNTIME") -Value "OpenWorkGraph $Version embedded Windows runtime" -Encoding ascii
Write-Host "Embedded Windows runtime: $Venv"
