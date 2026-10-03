from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_macos_bootstrap_delegates_to_existing_release_launcher():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "OpenWorkGraph-macOS.zip" in text
    assert "START_OPENWORKGRAPH.command" in text
    assert '/bin/bash "$START"' in text

    # The convenience app is only a locally-created launcher. It must point at
    # the stable installation created by START_ON_MAC.command, not own data.
    assert 'OpenWorkGraph.app' in text
    assert '$HOME/Library/Application Support/WorkflowObserver/START_ON_MAC.command' in text
    assert 'exec /usr/bin/open -a Terminal "$LAUNCHER"' in text
    assert "WORKFLOW_OBSERVER_DATA" not in text
    assert "config.json" not in text


def test_windows_bootstrap_delegates_to_existing_release_launcher():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "OpenWorkGraph-Windows.zip" in text
    assert "START_OPENWORKGRAPH.cmd" in text
    assert "& cmd.exe /d /c" in text

    # The Start-menu shortcut must reopen the same stable per-user installation.
    assert 'Join-Path $env:LOCALAPPDATA "OpenWorkGraph"' in text
    assert 'START_ON_WINDOWS.bat' in text
    assert "WScript.Shell" in text
    assert "WORKFLOW_OBSERVER_DATA" not in text
    assert "config.json" not in text


def test_existing_platform_launchers_still_preserve_user_state_on_upgrade():
    mac = (ROOT / "START_ON_MAC.command").read_text(encoding="utf-8")
    windows = (ROOT / "START_ON_WINDOWS.ps1").read_text(encoding="utf-8")

    # The easy installer must continue to inherit these preservation rules.
    assert "--exclude '.venv/'" in mac
    assert "--exclude '.runtime/'" in mac
    assert "--exclude 'data/'" in mac
    assert "--exclude 'config.json'" in mac

    assert '".venv"' in windows
    assert '".runtime"' in windows
    assert '"data"' in windows
    assert '"/XF", "config.json"' in windows
