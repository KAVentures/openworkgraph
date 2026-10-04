$VersionFile = Join-Path $env:LOCALAPPDATA "OpenWorkGraph\.openworkgraph-src\VERSION"
$Tray = Join-Path $env:LOCALAPPDATA "OpenWorkGraph\.openworkgraph-src\windows_tray.py"
$Managed = Join-Path $env:LOCALAPPDATA "OpenWorkGraph\managed.json"
if ((Test-Path $VersionFile) -and (Test-Path $Tray) -and (Test-Path $Managed)) {
  Write-Output ("OpenWorkGraph " + (Get-Content $VersionFile -Raw).Trim())
  exit 0
}
exit 1
