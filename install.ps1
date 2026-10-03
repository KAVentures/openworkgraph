$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Url = if ($env:OWG_INSTALL_URL) { $env:OWG_INSTALL_URL } else { "https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows.zip" }
$Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("openworkgraph-" + [guid]::NewGuid().ToString("N"))
$Zip = Join-Path $Temp "OpenWorkGraph-Windows.zip"
$Unpacked = Join-Path $Temp "unpacked"
$InstallDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph"
$StableLauncher = Join-Path $InstallDir "START_ON_WINDOWS.bat"
$ShortcutPath = $null

function Install-StartMenuShortcut {
    if ($env:OWG_INSTALL_NO_SHORTCUT -eq "1") { return }

    $Programs = [Environment]::GetFolderPath("Programs")
    if ([string]::IsNullOrWhiteSpace($Programs)) { return }

    $script:ShortcutPath = Join-Path $Programs "OpenWorkGraph.lnk"
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($script:ShortcutPath)
    $Shortcut.TargetPath = $StableLauncher
    $Shortcut.WorkingDirectory = $InstallDir
    $Shortcut.Description = "Open OpenWorkGraph"
    $Shortcut.Save()
    Write-Host "OpenWorkGraph shortcut installed in the Start menu."
}

New-Item -ItemType Directory -Force -Path $Temp, $Unpacked | Out-Null
try {
    Write-Host "Downloading the latest OpenWorkGraph Windows build..."
    if ($env:OWG_INSTALL_ZIP_PATH) {
        Copy-Item -LiteralPath $env:OWG_INSTALL_ZIP_PATH -Destination $Zip
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Zip
    }
    Expand-Archive -Path $Zip -DestinationPath $Unpacked -Force

    $Start = Get-ChildItem -Path $Unpacked -Filter "START_OPENWORKGRAPH.cmd" -File -Recurse | Select-Object -First 1
    if (-not $Start) {
        throw "Could not find START_OPENWORKGRAPH.cmd in the release package."
    }

    # The shortcut points at the stable per-user installation created by the
    # existing release launcher. It does not duplicate installation logic or
    # change where OpenWorkGraph stores its runtime, configuration or evidence.
    Install-StartMenuShortcut

    Write-Host "Starting OpenWorkGraph..."
    Write-Host "After this first setup, you can reopen it from the Start menu."
    & cmd.exe /d /c ('"' + $Start.FullName + '"')
    $ExitCode = $LASTEXITCODE

    if ($ExitCode -ne 0 -and $ShortcutPath -and -not (Test-Path $StableLauncher)) {
        Remove-Item -LiteralPath $ShortcutPath -Force -ErrorAction SilentlyContinue
    }
    exit $ExitCode
}
finally {
    Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
