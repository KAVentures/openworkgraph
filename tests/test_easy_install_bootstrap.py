from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_installers_are_pinned_to_repo_release_version():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    mac = (ROOT / "platform" / "distribution" / "installers" / "install.sh").read_text(encoding="utf-8")
    windows = (ROOT / "platform" / "distribution" / "installers" / "install.ps1").read_text(encoding="utf-8")

    assert f'RELEASE_VERSION="{version}"' in mac
    assert f'$ReleaseVersion = "{version}"' in windows
    assert f"/releases/download/v${{RELEASE_VERSION}}/OpenWorkGraph-macOS.zip" in mac
    assert "/releases/download/v$ReleaseVersion/OpenWorkGraph-Windows.zip" in windows
    assert "/releases/latest/download/OpenWorkGraph-" not in mac
    assert "/releases/latest/download/OpenWorkGraph-" not in windows


def test_macos_bootstrap_delegates_to_existing_release_launcher():
    text = (ROOT / "platform" / "distribution" / "installers" / "install.sh").read_text(encoding="utf-8")

    assert "OpenWorkGraph-macOS.zip" in text
    assert "START_OPENWORKGRAPH.command" in text
    assert 'TRY_DEMO_OPENWORKGRAPH.command' in text
    assert '/bin/bash "$START"' in text

    # The convenience app is only a locally-created launcher. It must point at
    # the stable installation created by START_ON_MAC.command, not own data.
    assert 'OpenWorkGraph.app' in text
    assert '$HOME/Library/Application Support/WorkflowObserver/START_ON_MAC.command' in text
    assert 'exec /usr/bin/open -a Terminal "$LAUNCHER"' in text
    assert 'OWG_SHORTCUT_TEST_DIRECT' in text
    assert "WORKFLOW_OBSERVER_DATA" not in text
    assert "config.json" not in text


def test_windows_bootstrap_delegates_to_existing_release_launcher():
    text = (ROOT / "platform" / "distribution" / "installers" / "install.ps1").read_text(encoding="utf-8")

    assert "OpenWorkGraph-Windows.zip" in text
    assert "START_OPENWORKGRAPH.cmd" in text
    assert "TRY_DEMO_OPENWORKGRAPH.cmd" in text
    assert "& cmd.exe /d /c" in text

    # The Start-menu shortcut must reopen the same stable per-user installation.
    assert 'Join-Path $env:LOCALAPPDATA "OpenWorkGraph"' in text
    assert 'START_ON_WINDOWS.bat' in text
    assert "WScript.Shell" in text
    assert "OWG_START_MENU_DIR" in text
    assert "WORKFLOW_OBSERVER_DATA" not in text
    assert "config.json" not in text


def test_existing_platform_launchers_still_preserve_user_state_on_upgrade():
    mac = (ROOT / "platform" / "distribution" / "launchers" / "START_ON_MAC.command").read_text(encoding="utf-8")
    windows = (ROOT / "platform" / "distribution" / "launchers" / "START_ON_WINDOWS.ps1").read_text(encoding="utf-8")

    # The easy installer must continue to inherit these preservation rules.
    assert "--exclude '.venv/'" in mac
    assert "--exclude '.runtime/'" in mac
    assert "--exclude 'data/'" in mac
    assert "--exclude 'config.json'" in mac

    assert '".venv"' in windows
    assert '".runtime"' in windows
    assert '"data"' in windows
    assert '"/XF", "config.json"' in windows


def test_uninstall_helpers_are_explicit_and_refuse_running_service():
    mac = (ROOT / "platform" / "distribution" / "installers" / "uninstall.sh").read_text(encoding="utf-8")
    windows = (ROOT / "platform" / "distribution" / "installers" / "uninstall.ps1").read_text(encoding="utf-8")

    assert "Type DELETE to continue" in mac
    assert "-iTCP:8787" in mac
    assert 'rm -rf "$INSTALL_DIR"' in mac

    assert 'Read-Host "Type DELETE to continue"' in windows
    assert 'BeginConnect("127.0.0.1", 8787' in windows
    assert 'Remove-Item -LiteralPath $InstallDir -Recurse -Force' in windows


def test_release_publishes_matching_install_and_uninstall_assets():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for name in ("install.sh", "install.ps1", "uninstall.sh", "uninstall.ps1"):
        assert f"release-assets/{name}" in workflow

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "releases/latest/download/install.sh" in readme
    assert "releases/latest/download/install.ps1" in readme
    assert "raw.githubusercontent.com/KAVentures/openworkgraph/main/install" not in readme


def test_packaged_smoke_runs_bootstrap_shortcut_and_uninstall():
    workflow = (ROOT / ".github" / "workflows" / "package-launch-smoke.yml").read_text(encoding="utf-8")
    assert "OWG_INSTALL_ZIP_PATH" in workflow
    assert "Start-menu shortcut" in workflow
    assert "Applications launcher" in workflow
    assert "uninstall.ps1" in workflow
    assert "uninstall.sh --yes" in workflow
