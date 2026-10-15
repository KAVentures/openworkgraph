"""macOS notarization blockers: native foreground, menu onboarding, uninstall safety."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_macos_foreground_uses_native_appkit_not_apple_events(monkeypatch):
    from collector import platform as collector
    class Front:
        def localizedName(self): return "Google Chrome"
        def processIdentifier(self): return 2468
    class Workspace:
        def frontmostApplication(self): return Front()
    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace(
        NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: Workspace())
    ))
    monkeypatch.setitem(sys.modules, "ApplicationServices", types.SimpleNamespace(
        AXIsProcessTrusted=lambda: True,
        AXUIElementCreateApplication=lambda pid: ("AX", pid),
        AXUIElementCopyAttributeValue=lambda obj, key, _: (0, "Invoice – June") if key == "AXTitle"
            else (0, ("window", 3)),
    ))
    def forbidden(*args, **kwargs):
        raise AssertionError("macOS capture spawned AppleScript or a shell")
    monkeypatch.setattr(collector.subprocess, "check_output", forbidden)
    assert collector._macos_active_window() == collector.ActiveWindow("Google Chrome", "Invoice – June")

    monkeypatch.setitem(sys.modules, "ApplicationServices", types.SimpleNamespace(
        AXIsProcessTrusted=lambda: False
    ))
    assert collector._macos_active_window() == collector.ActiveWindow("Google Chrome", "")


def test_macos_capture_degrades_without_leaking_window_content(monkeypatch):
    from collector import platform as collector
    class Front:
        def localizedName(self): return "Mail"
        def processIdentifier(self): return 512
    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace(
        NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: types.SimpleNamespace(
            frontmostApplication=lambda: Front()))
    ))
    def fail(*a):
        raise PermissionError("Accessibility denied")
    monkeypatch.setitem(sys.modules, "ApplicationServices", types.SimpleNamespace(
        AXIsProcessTrusted=lambda: True,
        AXUIElementCreateApplication=lambda _: "app",
        AXUIElementCopyAttributeValue=fail,
    ))
    assert collector._macos_active_window() == collector.ActiveWindow("Mail", "")


def test_signed_bundle_has_permission_and_uninstaller_paths():
    src=(ROOT/"scripts/build_macos_app_bundle.sh").read_text()
    collector=(ROOT/"collector/platform.py").read_text()
    assert "osascript" not in collector
    assert "AXFocusedWindow" in collector and "AXTitle" in collector
    assert "NSWorkspace" in collector
    assert "NSAccessibilityUsageDescription" in src
    assert "NSInputMonitoringUsageDescription" in src
    assert 'action: #selector(showPermissions)' in src
    assert 'action: #selector(uninstallApp)' in src
    assert 'x-apple.systempreferences:' in src
    assert "Port 8787 is already in use" in src
    assert "UNINSTALL_OPENWORKGRAPH.command" in src


@pytest.mark.skipif(os.name != "posix", reason="macOS bash uninstaller, POSIX only")
def test_uninstall_removes_all_managed_clients_before_deleting_runtime(tmp_path):
    home=tmp_path/"home"
    home.mkdir()
    install=home/"Library"/"Application Support"/"WorkflowObserver"
    install.mkdir(parents=True)
    (install/"owg_connect.py").write_text("placeholder")
    (install/"private_evidence.db").write_text("private")
    own_config=home/".claude"/"settings.json"
    own_config.parent.mkdir()
    own_config.write_text(json.dumps({"user_hook": "preserve-me"}))
    log=tmp_path/"calls.txt"
    stub=tmp_path/"fake-python"
    stub.write_text(f"#!/bin/bash\nprintf '%s %s %s\\n' \"$1\" \"$2\" \"$3\" >> {str(log)!r}\n")
    stub.chmod(0o700)
    env={**os.environ,"HOME":str(home),
         "OWG_UNINSTALL_INSTALL_DIR":str(install),
         "OWG_UNINSTALL_PYTHON":str(stub),
         "OWG_APPLICATIONS_DIR":str(tmp_path/"Apps"),
         "OWG_SYSTEM_APPLICATIONS_DIR":str(tmp_path/"SystemApps")}
    script=ROOT/"platform/distribution/installers/uninstall.sh"
    done=subprocess.run(["/bin/bash",str(script),"--yes"],env=env,
                        text=True,capture_output=True,timeout=30)
    assert done.returncode==0,done.stderr
    calls=log.read_text().splitlines()
    assert len(calls)==10
    assert all("remove " in line for line in calls)
    assert "claude_code" in " ".join(calls) and "codex" in " ".join(calls)
    assert not install.exists()
    assert json.loads(own_config.read_text())=={"user_hook":"preserve-me"}


@pytest.mark.skipif(os.name != "posix", reason="bash uninstaller")
def test_uninstall_fails_closed_if_client_cleanup_fails(tmp_path):
    install=tmp_path/"runtime"
    install.mkdir()
    (install/"owg_connect.py").write_text("placeholder")
    python=tmp_path/"bad-python"
    python.write_text("#!/bin/bash\nexit 7\n")
    python.chmod(0o700)
    env={**os.environ,"OWG_UNINSTALL_INSTALL_DIR":str(install),
         "OWG_UNINSTALL_PYTHON":str(python),"HOME":str(tmp_path),
         "OWG_APPLICATIONS_DIR":str(tmp_path/"Apps"),
         "OWG_SYSTEM_APPLICATIONS_DIR":str(tmp_path/"SystemApps")}
    done=subprocess.run(["/bin/bash",str(ROOT/"platform/distribution/installers/uninstall.sh"),"--yes"],
        env=env,text=True,capture_output=True,timeout=30)
    assert done.returncode != 0
    assert "runtime preserved" in done.stderr.lower()
    assert install.exists()


@pytest.mark.skipif(os.name != "posix", reason="bash uninstaller")
def test_uninstall_prefers_signed_bundle_python_over_system_python(tmp_path):
    """A signed .app must clean managed hooks using its own offline interpreter.

    A macOS machine can have /usr/bin/python3 installed without OWG's modules.
    If the uninstaller picks system Python first, cleanup fails and evidence
    remains on disk even though the app shipped a working interpreter.
    """
    home = tmp_path / "home"
    home.mkdir()
    install = home / "Library" / "Application Support" / "WorkflowObserver"
    install.mkdir(parents=True)
    (install / "owg_connect.py").write_text("this is deliberately not Python\n")

    apps = tmp_path / "Applications"
    payload = apps / "OpenWorkGraph.app" / "Contents" / "Resources" / "openworkgraph"
    runtime_python = payload / ".runtime" / "cpython-test" / "bin" / "python3.12"
    runtime_python.parent.mkdir(parents=True)
    log = tmp_path / "signed-python-calls.txt"
    runtime_python.write_text(
        f"#!/bin/sh\\nprintf '%s\\\\n' \\"$1:$2:$3\\" >> {str(log)!r}\\n"
    )
    runtime_python.chmod(0o700)
    (payload / "EMBEDDED_PYTHON.txt").write_text(
        ".runtime/cpython-test/bin/python3.12\\n"
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "OWG_UNINSTALL_INSTALL_DIR": str(install),
        "OWG_APPLICATIONS_DIR": str(tmp_path / "UserApplications"),
        "OWG_SYSTEM_APPLICATIONS_DIR": str(apps),
    }
    env.pop("OWG_UNINSTALL_PYTHON", None)
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "platform/distribution/installers/uninstall.sh"), "--yes"],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert len(log.read_text().splitlines()) == 10
    assert "claude_code" in log.read_text()
    assert "codex" in log.read_text()
    assert not install.exists()
