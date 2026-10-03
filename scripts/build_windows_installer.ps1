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
$Payload = Join-Path $Package ".openworkgraph-src"
$EmbeddedPythonw = Join-Path $Payload ".venv\Scripts\pythonw.exe"
$TrayHost = Join-Path $Payload "windows_tray.py"
if (-not (Test-Path $EmbeddedPythonw) -or -not (Test-Path $TrayHost)) {
    throw "Signed installer requires the embedded offline runtime. Run scripts/embed_windows_runtime.ps1 before this script."
}

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

[Tasks]
Name: "startup"; Description: "Start OpenWorkGraph when I sign in"; GroupDescription: "Startup"; Flags: checkedonce

[Icons]
Name: "{autoprograms}\OpenWorkGraph"; Filename: "{app}\.openworkgraph-src\.venv\Scripts\pythonw.exe"; Parameters: """{app}\.openworkgraph-src\windows_tray.py"""; WorkingDir: "{app}\.openworkgraph-src"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OpenWorkGraph"; ValueData: """{app}\.openworkgraph-src\.venv\Scripts\pythonw.exe"" ""{app}\.openworkgraph-src\windows_tray.py"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\.openworkgraph-src\.venv\Scripts\pythonw.exe"; Parameters: """{app}\.openworkgraph-src\windows_tray.py"""; WorkingDir: "{app}\.openworkgraph-src"; Description: "Start OpenWorkGraph"; Flags: postinstall nowait skipifsilent
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
