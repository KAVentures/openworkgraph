from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_macos_desktop_app_keeps_mutable_state_outside_signed_bundle():
    text = (ROOT / "scripts" / "build_macos_app_bundle.sh").read_text(encoding="utf-8")

    assert 'appendingPathComponent("WorkflowObserver", isDirectory: true)' in text
    assert 'rsync.arguments = [' in text
    assert '"--exclude", "data/"' in text
    assert '"--exclude", "config.json"' in text
    assert '"--exclude", ".runtime/"' in text
    assert 'installRoot.appendingPathComponent("start.py").path' in text
    assert 'process.currentDirectoryURL = installRoot' in text

    # The embedded interpreter remains inside the application bundle so an
    # installed/notarized app does not have to mutate or copy signed runtime code.
    assert 'bundlePayload.appendingPathComponent(relative)' in text


def test_macos_clickable_pkg_wraps_the_offline_app():
    text = (ROOT / "scripts" / "build_macos_installer_pkg.sh").read_text(encoding="utf-8")

    assert 'OpenWorkGraph.app' in text
    assert 'PKG_ROOT/Applications' in text
    assert 'pkgbuild' in text
    assert 'OpenWorkGraph-macOS-v$VERSION.pkg' in text
    assert 'shasum -a 256' in text


def test_windows_clickable_installer_is_offline_and_uninstallable():
    text = (ROOT / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")

    assert 'EMBEDDED_PYTHONW.txt' in text
    assert 'Uninstallable=yes' in text
    assert 'PrivilegesRequired=lowest' in text
    assert 'OpenWorkGraph-Windows-Setup-v$Version' in text
    assert 'Get-FileHash -Algorithm SHA256' in text


def test_normal_release_publishes_stable_one_click_aliases():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert 'bash scripts/build_macos_installer_pkg.sh' in workflow
    assert 'dist/OpenWorkGraph-macOS-v*.pkg' in workflow
    assert 'dist/OpenWorkGraph-Windows-Setup-v*.exe' in workflow

    for name in (
        "OpenWorkGraph-macOS.pkg",
        "OpenWorkGraph-macOS.pkg.sha256",
        "OpenWorkGraph-Windows-Setup.exe",
        "OpenWorkGraph-Windows-Setup.exe.sha256",
    ):
        assert f"release-assets/{name}" in workflow


def test_signed_workflow_can_replace_stable_aliases():
    workflow = (ROOT / ".github" / "workflows" / "signed-release.yml").read_text(encoding="utf-8")

    assert "contents: write" in workflow
    assert "publish-signed-installers:" in workflow
    assert "OpenWorkGraph-macOS.pkg" in workflow
    assert "OpenWorkGraph-Windows-Setup.exe" in workflow
    assert 'gh release upload "$TAG"' in workflow
    assert "--clobber" in workflow


def test_macos_app_registers_login_start_and_bounds_crash_recovery():
    text = (ROOT / "scripts" / "build_macos_app_bundle.sh").read_text(encoding="utf-8")

    assert "import ServiceManagement" in text
    assert "SMAppService.mainApp" in text
    assert "try service.register()" in text
    assert "try service.unregister()" in text
    assert "Start OpenWorkGraph at Login" in text
    assert "maxCrashRestarts = 5" in text
    assert "crashWindow: TimeInterval = 5 * 60" in text
    assert "handleUnexpectedExit" in text
    assert "keyAELaunchedAsLogInItem" in text
    assert 'startChild(openDashboard: !launchedAsLoginItem)' in text
    assert '"--no-open-dashboard"' in text
    assert "child = nil" in text
    assert "-framework ServiceManagement" in text

    # v0.121 uses the supported in-app Service Management registration path,
    # not a loose LaunchAgent installed beside the application.
    assert 'LAUNCH_AGENT="$DIST/com.kinvectum.openworkgraph.plist"' not in text


def test_windows_tray_recovers_only_unexpected_child_exits():
    text = (ROOT / "apps" / "desktop" / "windows_tray.py").read_text(encoding="utf-8")

    assert "_MAX_CRASH_RESTARTS = 5" in text
    assert "_RESTART_WINDOW_SECONDS = 5 * 60" in text
    assert "_restart_delay_after_crash" in text
    assert 'launched_in_background = "--background" in sys.argv[1:]' in text
    assert 'command.append("--no-open-dashboard")' in text
    assert "_CHILD is not child" in text
    assert "_SHUTTING_DOWN.set()" in text
    assert "_RESTART_TIMES.clear()" in text
    assert "Automatic restart is paused" in text


def test_windows_installer_still_defaults_to_start_at_sign_in():
    text = (ROOT / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")

    assert 'Name: "startup"' in text
    assert 'Description: "Start OpenWorkGraph when I sign in"' in text
    assert 'Flags: checkedonce' in text
    assert r"Software\Microsoft\Windows\CurrentVersion\Run" in text
    assert '--background' in text


def test_start_py_supports_silent_supervisor_launches():
    text = (ROOT / "start.py").read_text(encoding="utf-8")

    assert 'parser.add_argument("--no-open-dashboard", action="store_true")' in text
    assert text.count("if not args.no_open_dashboard:") == 2


def test_enterprise_windows_installer_is_machine_wide_but_keeps_user_state_writable():
    installer = (ROOT / "scripts" / "build_windows_enterprise_installer.ps1").read_text(encoding="utf-8")
    tray = (ROOT / "apps" / "desktop" / "windows_tray.py").read_text(encoding="utf-8")

    assert "PrivilegesRequired=admin" in installer
    assert "DefaultDirName={autopf}\\OpenWorkGraph" in installer
    assert "Root: HKLM" in installer
    assert "--machine --background" in installer
    assert "OpenWorkGraph-Windows-Enterprise-Setup-v$Version" in installer
    assert "_machine_runtime_root" in tray
    assert 'base / "OpenWorkGraph" / "Runtime"' in tray
    assert 'if "--machine" in sys.argv[1:]' in tray
    assert 'preserve = {"data", "config.json"}' in tray


def test_enterprise_release_contains_browser_and_mdm_assets():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    builder = (ROOT / "scripts" / "build_enterprise_release.py").read_text(encoding="utf-8")

    assert "enterprise-package:" in workflow
    assert "OpenWorkGraph-Browser-Sensor.zip" in workflow
    assert "OpenWorkGraph-Enterprise-Deployment-Kit.zip" in workflow
    assert "OpenWorkGraph-Windows-Enterprise-Setup.exe" in workflow
    assert "pairing.json" in builder
    assert "OpenWorkGraph-Browser-Sensor-v" in builder
    assert "OpenWorkGraph-Enterprise-Deployment-Kit-v" in builder


def test_signed_release_covers_consumer_and_enterprise_windows_installers():
    workflow = (ROOT / ".github" / "workflows" / "signed-release.yml").read_text(encoding="utf-8")

    assert "build_windows_enterprise_installer.ps1" in workflow
    assert "OpenWorkGraph-Windows-Enterprise-Setup-v$Version.exe" in workflow
    assert "OpenWorkGraph-Windows-Enterprise-Setup.exe" in workflow
