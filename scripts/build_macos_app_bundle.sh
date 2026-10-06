#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DIST="$ROOT/dist"
APP="$DIST/OpenWorkGraph.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
PAYLOAD="$RESOURCES/openworkgraph"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required at build time. Use astral-sh/setup-uv in CI." >&2
  exit 2
}
command -v swiftc >/dev/null 2>&1 || {
  echo "swiftc is required to build the macOS menu-bar launcher." >&2
  exit 2
}

rm -rf "$APP"
mkdir -p "$MACOS" "$PAYLOAD"

rsync -a   --exclude '.git/'   --exclude '.github/'   --exclude '.venv/'   --exclude '.runtime/'   --exclude '.pytest_cache/'   --exclude '__pycache__/'   --exclude 'data/'   --exclude 'dist/'   --exclude 'tests/'   --exclude 'config.json'   "$ROOT/" "$PAYLOAD/"

# Materialize the same historical payload-root compatibility surface as the
# tester ZIP builders.
cp "$ROOT/integrations/agents/owg_connect.py" "$PAYLOAD/owg_connect.py"
cp "$ROOT/integrations/agents/owg_bootstrap.sh" "$PAYLOAD/owg_bootstrap.sh"
cp "$ROOT/integrations/agents/owg_bootstrap.ps1" "$PAYLOAD/owg_bootstrap.ps1"
cp "$ROOT/platform/distribution/launchers/TRY_DEMO_ON_MAC.command" "$PAYLOAD/TRY_DEMO_ON_MAC.command"
cp "$ROOT/platform/distribution/launchers/TRY_DEMO_ON_WINDOWS.bat" "$PAYLOAD/TRY_DEMO_ON_WINDOWS.bat"
cp "$ROOT/platform/distribution/launchers/ADD_BROWSER_SENSOR.command" "$PAYLOAD/ADD_BROWSER_SENSOR.command"
cp "$ROOT/platform/distribution/launchers/ADD_BROWSER_SENSOR_WINDOWS.bat" "$PAYLOAD/ADD_BROWSER_SENSOR_WINDOWS.bat"
cp "$ROOT/apps/desktop/windows_tray.py" "$PAYLOAD/windows_tray.py"
cp "$ROOT/apps/desktop/demo_data.py" "$PAYLOAD/demo_data.py"
cp "$ROOT/apps/desktop/start.py" "$PAYLOAD/start.py"
cp "$ROOT/apps/desktop/config.example.json" "$PAYLOAD/config.example.json"
cp "$ROOT/platform/distribution/launchers/START_ON_MAC.command" "$PAYLOAD/START_ON_MAC.command"
cp "$ROOT/platform/distribution/launchers/START_ON_WINDOWS.bat" "$PAYLOAD/START_ON_WINDOWS.bat"
cp "$ROOT/platform/distribution/launchers/START_ON_WINDOWS.ps1" "$PAYLOAD/START_ON_WINDOWS.ps1"
cp "$ROOT/platform/distribution/installers/install.sh" "$PAYLOAD/install.sh"
cp "$ROOT/platform/distribution/installers/install.ps1" "$PAYLOAD/install.ps1"
cp "$ROOT/platform/distribution/installers/uninstall.sh" "$PAYLOAD/uninstall.sh"
cp "$ROOT/platform/distribution/installers/uninstall.ps1" "$PAYLOAD/uninstall.ps1"

BUILD_SHA="${GITHUB_SHA:-}"
if [[ -z "$BUILD_SHA" ]]; then
  BUILD_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ -n "$BUILD_SHA" ]]; then
  printf '%s
' "$BUILD_SHA" > "$PAYLOAD/BUILD_COMMIT"
fi

RUNTIME_ROOT="$PAYLOAD/.runtime/python"
mkdir -p "$RUNTIME_ROOT"
uv python install 3.12 --install-dir "$RUNTIME_ROOT"
PYTHON="$(find "$RUNTIME_ROOT" -type f -path '*/bin/python3.12' -print -quit)"
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "Embedded CPython executable was not found under $RUNTIME_ROOT" >&2
  exit 2
fi

# uv may create convenience aliases beside the real versioned runtime. Those
# symlinks are unnecessary in the app bundle and can make codesign reject the
# bundle as having an invalid symlink destination, so ship only the real tree.
find "$RUNTIME_ROOT" -maxdepth 1 -type l -delete

pushd "$PAYLOAD" >/dev/null
uv pip install --python "$PYTHON" --system --break-system-packages --link-mode copy .
"$PYTHON" - <<'PY'
import fastapi, mcp
import server.secure_app, collector.main
print("embedded macOS runtime imports OK")
PY
popd >/dev/null
PYTHON_RELATIVE="${PYTHON#"$PAYLOAD/"}"
printf '%s\n' "$PYTHON_RELATIVE" > "$PAYLOAD/EMBEDDED_PYTHON.txt"
printf 'OpenWorkGraph %s embedded macOS CPython runtime\n' "$VERSION" > "$PAYLOAD/OFFLINE_RUNTIME"

cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key><string>OpenWorkGraph</string>
  <key>CFBundleExecutable</key><string>OpenWorkGraph</string>
  <key>CFBundleIdentifier</key><string>com.kinvectum.openworkgraph</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>OpenWorkGraph</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>Copyright 2026 Koyar Afrasyab (Kinvectum)</string>
</dict>
</plist>
EOF

SWIFT_SOURCE="$DIST/OpenWorkGraphLauncher.swift"
cat > "$SWIFT_SOURCE" <<'SWIFT'
import Cocoa
import Foundation
import Darwin
import ServiceManagement

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var startAtLoginItem: NSMenuItem!
    private var child: Process?
    private var pendingRestart: DispatchWorkItem?
    private var crashTimes: [Date] = []
    private var isQuitting = false

    private let crashWindow: TimeInterval = 5 * 60
    private let maxCrashRestarts = 5
    private let loginPreferenceKey = "openworkgraph.startAtLoginConfigured"

    private var bundlePayload: URL {
        Bundle.main.resourceURL!.appendingPathComponent("openworkgraph", isDirectory: true)
    }

    private var installRoot: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Application Support", isDirectory: true)
            .appendingPathComponent("WorkflowObserver", isDirectory: true)
    }

    private var python: URL {
        let marker = bundlePayload.appendingPathComponent("EMBEDDED_PYTHON.txt")
        let contents = (try? String(contentsOf: marker, encoding: .utf8)) ?? ""
        let relative = contents.trimmingCharacters(in: .whitespacesAndNewlines)
        return bundlePayload.appendingPathComponent(relative)
    }

    private func prepareStablePayload() throws {
        let fm = FileManager.default
        try fm.createDirectory(at: installRoot, withIntermediateDirectories: true)

        // Keep the signed runtime sealed inside the app bundle, but run the
        // OpenWorkGraph source from the same stable user-writable installation
        // used by the existing launcher. Preserve local evidence and config.
        let rsync = Process()
        rsync.executableURL = URL(fileURLWithPath: "/usr/bin/rsync")
        rsync.arguments = [
            "-a", "--delete",
            "--exclude", ".venv/",
            "--exclude", ".runtime/",
            "--exclude", ".pytest_cache/",
            "--exclude", "__pycache__/",
            "--exclude", "data/",
            "--exclude", "config.json",
            bundlePayload.path + "/",
            installRoot.path + "/",
        ]
        try rsync.run()
        rsync.waitUntilExit()
        if rsync.terminationStatus != 0 {
            throw NSError(
                domain: "OpenWorkGraph",
                code: Int(rsync.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: "Could not prepare the local OpenWorkGraph installation."]
            )
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "OWG"
        statusItem.button?.toolTip = "OpenWorkGraph"

        let menu = NSMenu()

        let openDashboard = NSMenuItem(title: "Open dashboard", action: #selector(openDashboard), keyEquivalent: "o")
        openDashboard.target = self
        menu.addItem(openDashboard)

        let restart = NSMenuItem(title: "Restart OpenWorkGraph", action: #selector(restartApp), keyEquivalent: "r")
        restart.target = self
        menu.addItem(restart)

        menu.addItem(.separator())

        startAtLoginItem = NSMenuItem(
            title: "Start OpenWorkGraph at Login",
            action: #selector(toggleStartAtLogin),
            keyEquivalent: ""
        )
        startAtLoginItem.target = self
        menu.addItem(startAtLoginItem)

        menu.addItem(.separator())

        let quit = NSMenuItem(title: "Quit OpenWorkGraph", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu

        let event = NSAppleEventManager.shared().currentAppleEvent
        let launchedAsLoginItem =
            event?.eventID == kAEOpenApplication &&
            event?.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem

        configureStartAtLogin()
        startChild(openDashboard: !launchedAsLoginItem)
    }

    private func configureStartAtLogin() {
        let defaults = UserDefaults.standard
        let service = SMAppService.mainApp

        // Existing users get the reliable behavior on their first v0.121+
        // launch. After that, never silently reverse the user's explicit choice.
        if defaults.object(forKey: loginPreferenceKey) == nil {
            do {
                if service.status == .notRegistered {
                    try service.register()
                }
                defaults.set(true, forKey: loginPreferenceKey)
            } catch {
                // Registration can require user approval. Keep the app usable
                // and expose the current state in the menu instead of failing launch.
                defaults.set(true, forKey: loginPreferenceKey)
            }
        }

        refreshStartAtLoginMenu()
    }

    private func refreshStartAtLoginMenu() {
        guard startAtLoginItem != nil else { return }
        let status = SMAppService.mainApp.status
        startAtLoginItem.state = (status == .enabled || status == .requiresApproval) ? .on : .off
        if status == .requiresApproval {
            startAtLoginItem.title = "Start OpenWorkGraph at Login (approval required)"
        } else {
            startAtLoginItem.title = "Start OpenWorkGraph at Login"
        }
    }

    @objc private func toggleStartAtLogin() {
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled || service.status == .requiresApproval {
                try service.unregister()
                UserDefaults.standard.set(false, forKey: loginPreferenceKey)
            } else {
                try service.register()
                UserDefaults.standard.set(true, forKey: loginPreferenceKey)
            }
        } catch {
            let alert = NSAlert()
            alert.messageText = "Could not change Start at Login"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
        refreshStartAtLoginMenu()
    }

    @objc private func openDashboard() {
        guard let url = URL(string: "http://127.0.0.1:8787") else { return }
        NSWorkspace.shared.open(url)
    }

    private func terminateTree() {
        pendingRestart?.cancel()
        pendingRestart = nil

        guard let process = child else { return }
        // Clear ownership before terminating so the process termination callback
        // knows this was intentional and must not resurrect the child.
        child = nil

        if process.isRunning {
            let pid = process.processIdentifier
            let pkill = Process()
            pkill.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
            pkill.arguments = ["-TERM", "-P", String(pid)]
            try? pkill.run()
            pkill.waitUntilExit()
            process.terminate()
            for _ in 0..<20 {
                if !process.isRunning { break }
                usleep(100_000)
            }
            if process.isRunning {
                kill(pid, SIGKILL)
            }
        }
    }

    private func startChild(openDashboard: Bool) {
        if child?.isRunning == true || isQuitting { return }

        let process = Process()
        process.terminationHandler = { [weak self, weak process] _ in
            guard let self, let process else { return }
            DispatchQueue.main.async {
                self.handleUnexpectedExit(process)
            }
        }

        do {
            try prepareStablePayload()
            process.executableURL = python
            process.arguments = [
                installRoot.appendingPathComponent("start.py").path,
                "--mode", "observe",
            ] + (openDashboard ? [] : ["--no-open-dashboard"])
            process.currentDirectoryURL = installRoot
            try process.run()
            child = process
        } catch {
            let alert = NSAlert()
            alert.messageText = "OpenWorkGraph could not start"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
    }

    private func handleUnexpectedExit(_ process: Process) {
        guard !isQuitting, child === process else { return }
        child = nil

        let now = Date()
        crashTimes = crashTimes.filter { now.timeIntervalSince($0) < crashWindow }
        crashTimes.append(now)

        guard crashTimes.count <= maxCrashRestarts else {
            let alert = NSAlert()
            alert.messageText = "OpenWorkGraph stopped repeatedly"
            alert.informativeText = "Automatic restart was paused after repeated failures. Choose Restart OpenWorkGraph from the menu after checking the installation."
            alert.runModal()
            return
        }

        let delays: [TimeInterval] = [1, 2, 4, 8, 15]
        let delay = delays[min(crashTimes.count - 1, delays.count - 1)]
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.isQuitting else { return }
            self.pendingRestart = nil
            self.startChild(openDashboard: false)
        }
        pendingRestart?.cancel()
        pendingRestart = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    @objc private func restartApp() {
        crashTimes.removeAll()
        terminateTree()
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.isQuitting else { return }
            self.pendingRestart = nil
            self.startChild(openDashboard: true)
        }
        pendingRestart = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: work)
    }

    @objc private func quitApp() {
        isQuitting = true
        terminateTree()
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        isQuitting = true
        terminateTree()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
SWIFT

swiftc -O -framework Cocoa -framework ServiceManagement "$SWIFT_SOURCE" -o "$MACOS/OpenWorkGraph"
chmod +x "$MACOS/OpenWorkGraph"
rm -f "$SWIFT_SOURCE"

plutil -lint "$CONTENTS/Info.plist"
echo "Built offline macOS app bundle: $APP"
