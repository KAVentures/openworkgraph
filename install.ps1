$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ReleaseVersion = "0.120.0"
$Url = if ($env:OWG_INSTALL_URL) { $env:OWG_INSTALL_URL } else { "https://github.com/KAVentures/openworkgraph/releases/download/v$ReleaseVersion/OpenWorkGraph-Windows.zip" }
$Mode = if ($env:OWG_INSTALL_MODE) { $env:OWG_INSTALL_MODE } else { "observe" }
if ($Mode -notin @("observe", "demo")) {
    throw "OWG_INSTALL_MODE must be observe or demo."
}

$Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("openworkgraph-" + [guid]::NewGuid().ToString("N"))
$Zip = Join-Path $Temp "OpenWorkGraph-Windows.zip"
$Unpacked = Join-Path $Temp "unpacked"
$InstallDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph"
$StableLauncher = Join-Path $InstallDir "START_ON_WINDOWS.bat"
$ShortcutPath = $null
$ShortcutExisted = $false

function Install-StartMenuShortcut {
    if ($env:OWG_INSTALL_NO_SHORTCUT -eq "1") { return }

    $Programs = if ($env:OWG_START_MENU_DIR) {
        $env:OWG_START_MENU_DIR
    } else {
        [Environment]::GetFolderPath("Programs")
    }
    if ([string]::IsNullOrWhiteSpace($Programs)) { return }

    New-Item -ItemType Directory -Force -Path $Programs | Out-Null
    $script:ShortcutPath = Join-Path $Programs "OpenWorkGraph.lnk"
    $script:ShortcutExisted = Test-Path $script:ShortcutPath

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
    Write-Host "Downloading OpenWorkGraph v$ReleaseVersion for Windows..."
    if ($env:OWG_INSTALL_ZIP_PATH) {
        Copy-Item -LiteralPath $env:OWG_INSTALL_ZIP_PATH -Destination $Zip
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Zip
    }
    Expand-Archive -Path $Zip -DestinationPath $Unpacked -Force

    $LauncherName = if ($Mode -eq "demo") { "TRY_DEMO_OPENWORKGRAPH.cmd" } else { "START_OPENWORKGRAPH.cmd" }
    $Start = Get-ChildItem -Path $Unpacked -Filter $LauncherName -File -Recurse | Select-Object -First 1
    if (-not $Start) {
        throw "Could not find $LauncherName in the release package."
    }

    # The shortcut points at the stable per-user installation created by the
    # existing release launcher. It does not duplicate installation logic or
    # change where OpenWorkGraph stores its runtime, configuration or evidence.
    Install-StartMenuShortcut

    Write-Host "Starting OpenWorkGraph..."
    Write-Host "After this first setup, you can reopen it from the Start menu."
    & cmd.exe /d /c ('"' + $Start.FullName + '"')
    $ExitCode = $LASTEXITCODE

    if ($ExitCode -ne 0 -and $ShortcutPath -and -not $ShortcutExisted) {
        Remove-Item -LiteralPath $ShortcutPath -Force -ErrorAction SilentlyContinue
    }
    exit $ExitCode
}
finally {
    Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
