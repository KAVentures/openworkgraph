$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Dist = Join-Path $Root "dist"
$Package = Join-Path $Dist "OpenWorkGraph-Windows-v$Version"
$OutputBase = "OpenWorkGraph-Windows-Enterprise-Setup-v$Version"

if (-not (Test-Path $Package)) { throw "Expected $Package. Run scripts/build_windows_release.ps1 first." }
$Payload = Join-Path $Package ".openworkgraph-src"
$PythonwRelative = (Get-Content (Join-Path $Payload "EMBEDDED_PYTHONW.txt") -Raw).Trim()
$PythonwInApp = "{app}\.openworkgraph-src\" + $PythonwRelative.Replace("/", "\")

$Iscc = $env:ISCC_PATH
if (-not $Iscc) {
    $ProgramFilesX86 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
    $ProgramFiles64 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
    $Candidates = @(
        (Join-Path $ProgramFilesX86 "Inno Setup 6\ISCC.exe"),
        (Join-Path $ProgramFiles64 "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ChocolateyInstall "bin\ISCC.exe")
    ) | Where-Object { $_ }
    $Iscc = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) {
        $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($Command) { $Iscc = $Command.Source }
    }
}
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    throw "Inno Setup 6 (ISCC.exe) is required. Set ISCC_PATH if it is installed elsewhere."
}

$Iss = Join-Path $Dist "openworkgraph-enterprise-installer.iss"
$PackageEscaped = $Package.Replace("\", "\\")
$DistEscaped = $Dist.Replace("\", "\\")

@"
#define MyAppName "OpenWorkGraph"
#define MyAppVersion "$Version"
#define MyAppPublisher "Kinvectum"
#define MyAppURL "https://owg.kinvectum.com"

[Setup]
AppId={{7E4E40C2-C518-4F77-A35D-3A0C9A2C9981}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\OpenWorkGraph
DisableProgramGroupPage=yes
PrivilegesRequired=admin
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

[Tasks]
Name: "startup"; Description: "Start OpenWorkGraph for users when they sign in"; GroupDescription: "Startup"; Flags: checkedonce

[Icons]
Name: "{commonprograms}\OpenWorkGraph"; Filename: "$PythonwInApp"; Parameters: """{app}\.openworkgraph-src\windows_tray.py"" --machine"; WorkingDir: "{app}\.openworkgraph-src"

[Registry]
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OpenWorkGraph"; ValueData: """$PythonwInApp"" ""{app}\.openworkgraph-src\windows_tray.py"" --machine --background"; Flags: uninsdeletevalue; Tasks: startup
"@ | Set-Content -Path $Iss -Encoding utf8

& $Iscc $Iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$Installer = Join-Path $Dist "$OutputBase.exe"
if (-not (Test-Path $Installer)) { throw "Installer was not created: $Installer" }
$Hash = (Get-FileHash -Algorithm SHA256 $Installer).Hash.ToLowerInvariant()
"$Hash  $(Split-Path $Installer -Leaf)" | Set-Content "$Installer.sha256" -Encoding ascii
Write-Host "Built Windows enterprise installer: $Installer"
