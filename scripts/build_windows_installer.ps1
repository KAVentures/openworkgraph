$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Dist = Join-Path $Root "dist"
$Package = Join-Path $Dist "OpenWorkGraph-Windows-v$Version"
$OutputBase = "OpenWorkGraph-Windows-Setup-v$Version"

if (-not (Test-Path $Package)) {
    throw "Expected $Package. Run scripts/build_windows_release.ps1 first."
}

$Iscc = $env:ISCC_PATH
if (-not $Iscc) {
    $Candidates = @(
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    throw "Inno Setup 6 (ISCC.exe) is required. Set ISCC_PATH if it is installed elsewhere."
}

$Iss = Join-Path $Dist "openworkgraph-installer.iss"
$PackageEscaped = $Package.Replace("\", "\\")
$DistEscaped = $Dist.Replace("\", "\\")

@"
#define MyAppName "OpenWorkGraph"
#define MyAppVersion "$Version"
#define MyAppPublisher "Kinvectum"
#define MyAppURL "https://owg.kinvectum.com"

[Setup]
AppId={{4E5A9D1A-6BA4-4F99-A460-34A9E0EE7E91}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={localappdata}\OpenWorkGraph
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=$DistEscaped
OutputBaseFilename=$OutputBase
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=$PackageEscaped\\LICENSE

[Files]
Source: "$PackageEscaped\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OpenWorkGraph"; Filename: "{cmd}"; Parameters: "/c ""{app}\START_OPENWORKGRAPH.cmd"""; WorkingDir: "{app}"

[Run]
Filename: "{cmd}"; Parameters: "/c ""{app}\START_OPENWORKGRAPH.cmd"""; WorkingDir: "{app}"; Description: "Start OpenWorkGraph"; Flags: postinstall nowait skipifsilent
"@ | Set-Content -Path $Iss -Encoding utf8

& $Iscc $Iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

$Installer = Join-Path $Dist "$OutputBase.exe"
if (-not (Test-Path $Installer)) {
    throw "Installer was not created: $Installer"
}

Write-Host "Built Windows installer: $Installer"
