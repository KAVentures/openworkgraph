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

$RuntimeRoot = Join-Path $Payload ".runtime\python"
if (Test-Path $RuntimeRoot) {
    Remove-Item $RuntimeRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

& $Uv python install 3.12 --install-dir $RuntimeRoot
if ($LASTEXITCODE -ne 0) { throw "uv python install failed with exit code $LASTEXITCODE" }

$Python = Get-ChildItem $RuntimeRoot -Recurse -Filter python.exe |
    Where-Object { $_.FullName -match "\\python\.exe$" } |
    Select-Object -First 1 -ExpandProperty FullName
$Pythonw = Get-ChildItem $RuntimeRoot -Recurse -Filter pythonw.exe |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $Python -or -not $Pythonw) {
    throw "Embedded CPython executables were not found under $RuntimeRoot"
}

Push-Location $Payload
try {
    & $Uv pip install --python $Python --system --break-system-packages --link-mode copy "."
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed with exit code $LASTEXITCODE" }

    & $Python -c "import fastapi, mcp, pystray, win32com.client, uiautomation; import server.secure_app, collector.main"
    if ($LASTEXITCODE -ne 0) { throw "embedded runtime import smoke test failed" }
} finally {
    Pop-Location
}

$PayloadPrefix = $Payload.TrimEnd("\\", "/") + [IO.Path]::DirectorySeparatorChar
if (-not $Pythonw.StartsWith($PayloadPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Embedded pythonw.exe is unexpectedly outside the packaged payload: $Pythonw"
}
$RelativePythonw = $Pythonw.Substring($PayloadPrefix.Length)
Set-Content -Path (Join-Path $Payload "EMBEDDED_PYTHONW.txt") -Value $RelativePythonw -Encoding ascii
Set-Content -Path (Join-Path $Payload "OFFLINE_RUNTIME") -Value "OpenWorkGraph $Version embedded Windows CPython runtime" -Encoding ascii
Write-Host "Embedded Windows runtime: $RuntimeRoot"
Write-Host "pythonw relative path: $RelativePythonw"
