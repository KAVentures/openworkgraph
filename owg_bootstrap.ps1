param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BootstrapArgs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallRoot = Join-Path $env:LOCALAPPDATA "OpenWorkGraph"
$Log = Join-Path ([System.IO.Path]::GetTempPath()) "openworkgraph-agent-bootstrap-installer.log"
$ErrLog = Join-Path ([System.IO.Path]::GetTempPath()) "openworkgraph-agent-bootstrap-installer.err.log"

function Find-OwgPython {
    $Venv = Join-Path $InstallRoot ".venv\Scripts\python.exe"
    if (Test-Path $Venv) { return $Venv }

    $Marker = Join-Path $InstallRoot "EMBEDDED_PYTHONW.txt"
    if (Test-Path $Marker) {
        $PythonWRel = (Get-Content -LiteralPath $Marker -Raw).Trim()
        if ($PythonWRel) {
            $PythonW = Join-Path $InstallRoot $PythonWRel
            $Python = Join-Path (Split-Path -Parent $PythonW) "python.exe"
            if (Test-Path $Python) { return $Python }
        }
    }

    $PayloadRoot = Join-Path $InstallRoot ".openworkgraph-src"
    $PayloadMarker = Join-Path $PayloadRoot "EMBEDDED_PYTHONW.txt"
    if (Test-Path $PayloadMarker) {
        $PythonWRel = (Get-Content -LiteralPath $PayloadMarker -Raw).Trim()
        if ($PythonWRel) {
            $PythonW = Join-Path $PayloadRoot $PythonWRel
            $Python = Join-Path (Split-Path -Parent $PythonW) "python.exe"
            if (Test-Path $Python) { return $Python }
        }
    }
    return $null
}

function Test-OwgHealth {
    try {
        $Health = Invoke-RestMethod -UseBasicParsing -Uri "http://127.0.0.1:8787/health" -TimeoutSec 2
        return $Health.status -eq "ok"
    } catch {
        return $false
    }
}

$Python = Find-OwgPython
$Installer = $null
$Launched = $false

if (-not $Python) {
    $InstallerScript = Join-Path $Root "install.ps1"
    $Installer = Start-Process powershell.exe -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $InstallerScript + '"')
    ) -RedirectStandardOutput $Log -RedirectStandardError $ErrLog -PassThru
    $Launched = $true

    for ($i = 0; $i -lt 240; $i++) {
        $Python = Find-OwgPython
        if ($Python) { break }
        if ($Installer.HasExited) {
            throw "OpenWorkGraph installation stopped before its private runtime was ready. Logs: $Log ; $ErrLog"
        }
        Start-Sleep -Seconds 1
    }
}

if (-not $Python) {
    throw "OpenWorkGraph private runtime was not provisioned. Logs: $Log ; $ErrLog"
}

if ($Launched) {
    for ($i = 0; $i -lt 240; $i++) {
        if (Test-OwgHealth) { break }
        if ($Installer.HasExited) {
            throw "OpenWorkGraph installer exited before the local service became healthy. Logs: $Log ; $ErrLog"
        }
        Start-Sleep -Seconds 1
    }
    if (-not (Test-OwgHealth)) {
        throw "OpenWorkGraph did not become healthy after installation. Logs: $Log ; $ErrLog"
    }
}

$env:OWG_INSTALLED_ROOT = $InstallRoot
$env:OWG_INSTALLED_PYTHON = $Python
& $Python (Join-Path $Root "owg_connect.py") bootstrap --local @BootstrapArgs
exit $LASTEXITCODE
