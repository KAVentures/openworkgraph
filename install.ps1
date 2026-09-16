$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Url = "https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows.zip"
$Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("openworkgraph-" + [guid]::NewGuid().ToString("N"))
$Zip = Join-Path $Temp "OpenWorkGraph-Windows.zip"
$Unpacked = Join-Path $Temp "unpacked"

New-Item -ItemType Directory -Force -Path $Temp, $Unpacked | Out-Null
try {
    Write-Host "Downloading the latest OpenWorkGraph Windows tester build..."
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Zip
    Expand-Archive -Path $Zip -DestinationPath $Unpacked -Force

    $Start = Get-ChildItem -Path $Unpacked -Filter "START_OPENWORKGRAPH.cmd" -File -Recurse | Select-Object -First 1
    if (-not $Start) {
        throw "Could not find START_OPENWORKGRAPH.cmd in the release package."
    }

    Write-Host "Starting OpenWorkGraph..."
    & cmd.exe /d /c ('"' + $Start.FullName + '"')
    exit $LASTEXITCODE
}
finally {
    Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
