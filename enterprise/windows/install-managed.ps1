param(
  [Parameter(Mandatory=$true)][string]$JoinCode,
  [string]$ActorId = "",
  [string]$EmailDomain = "",
  [switch]$MachineWide
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isSystem = $identity.IsSystem
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$useMachine = $MachineWide -or $isSystem

if ($useMachine -and -not ($isAdmin -or $isSystem)) {
  throw "Machine-wide OpenWorkGraph deployment requires administrator/SYSTEM context."
}

if ($useMachine) {
  $InstallerUrl = "https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows-Enterprise-Setup.exe"
  $HashUrl = "$InstallerUrl.sha256"
  $ManagedDir = Join-Path $env:ProgramData "OpenWorkGraph"
} else {
  $InstallerUrl = "https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-Windows-Setup.exe"
  $HashUrl = "$InstallerUrl.sha256"
  $ManagedDir = Join-Path $env:LOCALAPPDATA "OpenWorkGraph"
}

$Temp = Join-Path $env:TEMP "openworkgraph-enterprise"
New-Item -ItemType Directory -Path $Temp -Force | Out-Null
$Installer = Join-Path $Temp "OpenWorkGraph-Setup.exe"
$HashFile = Join-Path $Temp "OpenWorkGraph-Setup.exe.sha256"

Invoke-WebRequest -UseBasicParsing $InstallerUrl -OutFile $Installer
Invoke-WebRequest -UseBasicParsing $HashUrl -OutFile $HashFile
$Expected = ((Get-Content $HashFile -Raw).Trim() -split "\s+")[0].ToLowerInvariant()
$Actual = (Get-FileHash -Algorithm SHA256 $Installer).Hash.ToLowerInvariant()
if (-not $Expected -or $Expected -ne $Actual) {
  throw "OpenWorkGraph installer SHA-256 verification failed."
}

New-Item -ItemType Directory -Path $ManagedDir -Force | Out-Null
$Managed = Join-Path $ManagedDir "managed.json"
$Payload = [ordered]@{ join_code = $JoinCode; actor_id = $ActorId; email_domain = $EmailDomain }
$Payload | ConvertTo-Json | Set-Content -Path $Managed -Encoding UTF8

if ($useMachine) {
  & icacls.exe $Managed /inheritance:e /grant "*S-1-5-32-545:(R)" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not set managed configuration ACLs." }
}

$proc = Start-Process -FilePath $Installer -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-","/TASKS=startup" -PassThru -Wait
if ($proc.ExitCode -ne 0) { throw "OpenWorkGraph installer exited with $($proc.ExitCode)." }

if ($useMachine) {
  $VersionCandidates = @(
    (Join-Path $env:ProgramFiles "OpenWorkGraph\.openworkgraph-src\VERSION"),
    (Join-Path ${env:ProgramFiles(x86)} "OpenWorkGraph\.openworkgraph-src\VERSION")
  ) | Where-Object { $_ -and (Test-Path $_) }
  if (-not $VersionCandidates) { throw "Machine-wide OpenWorkGraph installed but VERSION could not be found." }
  $VersionFile = $VersionCandidates[0]
} else {
  $VersionFile = Join-Path $env:LOCALAPPDATA "OpenWorkGraph\.openworkgraph-src\VERSION"
  if (-not (Test-Path $VersionFile)) { throw "OpenWorkGraph installed but VERSION could not be found." }
}

$Scope = if ($useMachine) { "machine-wide" } else { "per-user" }
Write-Output "OpenWorkGraph $((Get-Content $VersionFile -Raw).Trim()) installed $Scope with managed enrollment."
