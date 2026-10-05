$UserVersion = Join-Path $env:LOCALAPPDATA "OpenWorkGraph\.openworkgraph-src\VERSION"
$MachineCandidates = @(
  (Join-Path $env:ProgramFiles "OpenWorkGraph\.openworkgraph-src\VERSION"),
  (Join-Path ${env:ProgramFiles(x86)} "OpenWorkGraph\.openworkgraph-src\VERSION")
) | Where-Object { $_ }

foreach ($VersionFile in @($MachineCandidates + $UserVersion)) {
  if ($VersionFile -and (Test-Path $VersionFile)) {
    Write-Output ("OpenWorkGraph " + (Get-Content $VersionFile -Raw).Trim())
    exit 0
  }
}
exit 1
