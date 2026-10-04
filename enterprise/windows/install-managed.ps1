param(
  [Parameter(Mandatory=$true)][string]$JoinCode,
  [string]$ActorId = "",
  [string]$EmailDomain = "",
  [string]$InstallerUrl = "https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows-Setup.exe",
  [string]$HashUrl = "https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows-Setup.exe.sha256"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# The public Windows installer is per-user. Running this as SYSTEM would install
# into the SYSTEM profile and appear successful while the employee sees nothing.
if ([Security.Principal.WindowsIdentity]::GetCurrent().IsSystem) {
  throw "OpenWorkGraph v0.122 managed Windows deployment must run in Intune/User context, not SYSTEM context."
}

$Temp = Join-Path $env:TEMP "openworkgraph-enterprise"
New-Item -ItemType Directory -Path $Temp -Force | Out-Null
$Installer = Join-Path $Temp "OpenWorkGraph-Windows-Setup.exe"
$HashFile = Join-Path $Temp "OpenWorkGraph-Windows-Setup.exe.sha256"

Invoke-WebRequest -UseBasicParsing $InstallerUrl -OutFile $Installer
Invoke-WebRequest -UseBasicParsing $HashUrl -OutFile $HashFile
$Expected = ((Get-Content $HashFile -Raw).Trim() -split "\s+")[0].ToLowerInvariant()
$Actual = (Get-FileHash -Algorithm SHA256 $Installer).Hash.ToLowerInvariant()
if (-not $Expected -or $Expected -ne $Actual) {
  throw "OpenWorkGraph installer SHA-256 verification failed."
}

$ManagedDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph"
New-Item -ItemType Directory -Path $ManagedDir -Force | Out-Null
$Managed = Join-Path $ManagedDir "managed.json"
$Payload = [ordered]@{ join_code = $JoinCode; actor_id = $ActorId; email_domain = $EmailDomain }
$Payload | ConvertTo-Json | Set-Content -Path $Managed -Encoding UTF8

$proc = Start-Process -FilePath $Installer -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-","/TASKS=startup" -PassThru -Wait
if ($proc.ExitCode -ne 0) { throw "OpenWorkGraph installer exited with $($proc.ExitCode)." }

$VersionFile = Join-Path $env:LOCALAPPDATA "OpenWorkGraph\.openworkgraph-src\VERSION"
if (-not (Test-Path $VersionFile)) { throw "OpenWorkGraph installed but VERSION could not be found." }
Write-Output "OpenWorkGraph $((Get-Content $VersionFile -Raw).Trim()) installed for $env:USERNAME with managed enrollment."
