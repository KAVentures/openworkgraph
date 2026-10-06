param(
    [ValidateSet("observe", "demo")]
    [string]$Mode = "observe"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $ScriptDir "pyproject.toml")) {
    $SourceDir = $ScriptDir
} else {
    $SourceDir = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path
}
$InstallDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph"
$RuntimeDir = Join-Path $InstallDir ".runtime"
$LogDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph-logs"
$LogFile = Join-Path $LogDir "setup.log"
$UvVersion = "0.12.15"
$UvBinDir = Join-Path $RuntimeDir "bin"
$UvBin = Join-Path $UvBinDir "uv.exe"

New-Item -ItemType Directory -Force -Path $InstallDir, $RuntimeDir, $LogDir | Out-Null

try {
    Start-Transcript -Path $LogFile -Append | Out-Null
} catch {
    # Logging is helpful but must never prevent startup.
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    Write-Host "Setup log: $LogFile"
    throw $Message
}

try {
    Write-Host "Preparing OpenWorkGraph in $InstallDir ..."

    # Fail before downloading anything when another service owns the local API port.
    # This is intentionally a plain TCP probe: the launcher must never send evidence
    # to an unknown localhost process merely because it happens to answer HTTP.
    $portBusy = $false
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", 8787, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(250, $false) -and $client.Connected) {
            $portBusy = $true
        }
        $client.Close()
    } catch {}
    if ($portBusy) {
        Fail "Port 8787 is already in use. Close the existing OpenWorkGraph window/service (or the other app using 127.0.0.1:8787), then launch OpenWorkGraph again."
    }

    $sourceFull = [System.IO.Path]::GetFullPath($SourceDir).TrimEnd('\')
    $installFull = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    if ($sourceFull -ne $installFull) {
        # Mirror application source while preserving local runtime, environment,
        # captured data and user configuration across upgrades.
        $robocopyArgs = @(
            $SourceDir,
            $InstallDir,
            "/MIR",
            "/R:2",
            "/W:1",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/NP",
            "/XD", ".git", ".github", ".venv", ".runtime", ".pytest_cache", "__pycache__", "data", "dist", "logs",
            "/XF", "config.json"
        )
        & robocopy @robocopyArgs | Out-Host
        # Robocopy codes 0-7 are success/informational; >=8 is failure.
        if ($LASTEXITCODE -ge 8) {
            Fail "Could not copy OpenWorkGraph into the local application folder (robocopy exit $LASTEXITCODE)."
        }
    }

    if ([System.IO.Path]::GetFullPath($ScriptDir).TrimEnd('\') -ne $sourceFull) {
        $compat = @{
            "apps\desktop\start.py" = "start.py"
            "apps\desktop\config.example.json" = "config.example.json"
            "apps\desktop\demo_data.py" = "demo_data.py"
            "apps\desktop\windows_tray.py" = "windows_tray.py"
            "integrations\agents\owg_connect.py" = "owg_connect.py"
            "integrations\agents\owg_bootstrap.sh" = "owg_bootstrap.sh"
            "integrations\agents\owg_bootstrap.ps1" = "owg_bootstrap.ps1"
            "platform\distribution\launchers\START_ON_MAC.command" = "START_ON_MAC.command"
            "platform\distribution\launchers\START_ON_WINDOWS.bat" = "START_ON_WINDOWS.bat"
            "platform\distribution\launchers\START_ON_WINDOWS.ps1" = "START_ON_WINDOWS.ps1"
            "platform\distribution\launchers\TRY_DEMO_ON_MAC.command" = "TRY_DEMO_ON_MAC.command"
            "platform\distribution\launchers\TRY_DEMO_ON_WINDOWS.bat" = "TRY_DEMO_ON_WINDOWS.bat"
            "platform\distribution\launchers\ADD_BROWSER_SENSOR.command" = "ADD_BROWSER_SENSOR.command"
            "platform\distribution\launchers\ADD_BROWSER_SENSOR_WINDOWS.bat" = "ADD_BROWSER_SENSOR_WINDOWS.bat"
            "platform\distribution\installers\install.sh" = "install.sh"
            "platform\distribution\installers\install.ps1" = "install.ps1"
            "platform\distribution\installers\uninstall.sh" = "uninstall.sh"
            "platform\distribution\installers\uninstall.ps1" = "uninstall.ps1"
        }
        foreach ($entry in $compat.GetEnumerator()) {
            Copy-Item (Join-Path $SourceDir $entry.Key) (Join-Path $InstallDir $entry.Value) -Force
        }
    }

    Set-Location $InstallDir

    $env:UV_UNMANAGED_INSTALL = $UvBinDir
    $env:UV_NO_MODIFY_PATH = "1"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeDir "python"
    $env:UV_CACHE_DIR = Join-Path $RuntimeDir "cache"

    if (-not (Test-Path $UvBin)) {
        Write-Host "Downloading runtime (first launch can take about 1 minute) ..."
        Write-Host "No system Python installation is required."
        New-Item -ItemType Directory -Force -Path $UvBinDir | Out-Null
        $installer = Join-Path $RuntimeDir "uv-install.ps1"
        Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/$UvVersion/install.ps1" -OutFile $installer
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $UvBin)) {
            Fail "Could not install the private uv runtime."
        }
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
    }

    $Python = Join-Path $InstallDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $Python)) {
        Write-Host "Downloading Python runtime (first launch can take about 1 minute) ..."
        Remove-Item (Join-Path $InstallDir ".venv") -Recurse -Force -ErrorAction SilentlyContinue
        & $UvBin venv --python 3.12 --managed-python .venv
        if ($LASTEXITCODE -ne 0) {
            Fail "Could not create OpenWorkGraph's private Python environment."
        }
    }

    Write-Host "Checking OpenWorkGraph dependencies ..."
    & $UvBin pip install --python $Python -e . --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) {
        Fail "Dependency installation failed."
    }

    if (-not (Test-Path "config.json")) {
        Copy-Item "config.example.json" "config.json"
    }

    if ($Mode -eq "demo") {
        Write-Host "Starting OpenWorkGraph demo ..."
    } else {
        Write-Host "Starting OpenWorkGraph ..."
    }
    Write-Host "The dashboard will open at http://127.0.0.1:8787"
    & $Python start.py --mode $Mode
    exit $LASTEXITCODE
}
catch {
    Write-Host ""
    Write-Host "OpenWorkGraph setup did not complete." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Setup log: $LogFile"
    exit 1
}
finally {
    try { Stop-Transcript | Out-Null } catch {}
}
