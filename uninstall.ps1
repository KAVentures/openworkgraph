param(
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$InstallDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph"
$LogDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph-logs"
$Programs = if ($env:OWG_START_MENU_DIR) {
    $env:OWG_START_MENU_DIR
} else {
    [Environment]::GetFolderPath("Programs")
}
$Shortcut = if ([string]::IsNullOrWhiteSpace($Programs)) { $null } else { Join-Path $Programs "OpenWorkGraph.lnk" }

$PortBusy = $false
try {
    $Client = New-Object System.Net.Sockets.TcpClient
    $Async = $Client.BeginConnect("127.0.0.1", 8787, $null, $null)
    if ($Async.AsyncWaitHandle.WaitOne(250, $false) -and $Client.Connected) {
        $PortBusy = $true
    }
    $Client.Close()
} catch {}

if ($PortBusy) {
    throw "Port 8787 is currently in use. Quit OpenWorkGraph (or the other service using that port) before uninstalling."
}

if (-not $Yes -and $env:OWG_UNINSTALL_YES -ne "1") {
    Write-Host "This will remove OpenWorkGraph, its local runtime, configuration, and recorded local data from:"
    Write-Host "  $InstallDir"
    Write-Host ""
    $Answer = Read-Host "Type DELETE to continue"
    if ($Answer -ne "DELETE") {
        Write-Host "Uninstall cancelled."
        exit 0
    }
}

if ($Shortcut) {
    Remove-Item -LiteralPath $Shortcut -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $LogDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "OpenWorkGraph has been removed from this Windows user account."
Write-Host "Browser extensions are managed by your browser; remove the OpenWorkGraph extension there if you installed it."
