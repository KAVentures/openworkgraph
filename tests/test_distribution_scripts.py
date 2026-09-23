from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_macos_signed_package_script_has_valid_bash_syntax():
    bash = shutil.which("bash")
    if not bash:
        return
    subprocess.run(
        [bash, "-n", str(ROOT / "scripts" / "build_macos_signed_pkg.sh")],
        check=True,
        cwd=ROOT,
    )


def test_windows_installer_script_parses_on_windows():
    if platform.system() != "Windows":
        return
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh, "PowerShell is required on the Windows CI runner"
    script = ROOT / "scripts" / "build_windows_installer.ps1"
    command = (
        "$errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    subprocess.run([pwsh, "-NoProfile", "-Command", command], check=True, cwd=ROOT)


def test_signed_installer_workflow_is_manual_only():
    workflow = (ROOT / ".github" / "workflows" / "signed-release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "APPLE_DEVELOPER_ID_INSTALLER_P12_BASE64" in workflow
    assert "WINDOWS_SIGNING_PFX_BASE64" in workflow
