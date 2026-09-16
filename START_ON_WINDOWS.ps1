$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph"
$RuntimeDir = Join-Path $InstallDir ".runtime"
$LogDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph\logs"
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
            "/XD", ".git", ".github", ".venv", ".runtime", ".pytest_cache", "__pycache__", "data", "dist",
            "/XF", "config.json"
        )
        & robocopy @robocopyArgs | Out-Host
        # Robocopy codes 0-7 are success/informational; >=8 is failure.
        if ($LASTEXITCODE -ge 8) {
            Fail "Could not copy OpenWorkGraph into the local application folder (robocopy exit $LASTEXITCODE)."
        }
    }

    Set-Location $InstallDir

    $env:UV_UNMANAGED_INSTALL = $UvBinDir
    $env:UV_NO_MODIFY_PATH = "1"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeDir "python"
    $env:UV_CACHE_DIR = Join-Path $RuntimeDir "cache"

    if (-not (Test-Path $UvBin)) {
        Write-Host "First-time setup: installing OpenWorkGraph's private runtime manager ..."
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
        Write-Host "First-time setup: downloading OpenWorkGraph's private Python runtime ..."
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

    Write-Host "Starting OpenWorkGraph ..."
    Write-Host "The dashboard will open at http://127.0.0.1:8787"
    & $Python start.py --mode observe
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
