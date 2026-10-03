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
LAUNCH_AGENT="$DIST/com.kinvectum.openworkgraph.plist"

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

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var child: Process?

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
        let restart = NSMenuItem(title: "Restart and open dashboard", action: #selector(restartApp), keyEquivalent: "r")
        restart.target = self
        menu.addItem(restart)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit OpenWorkGraph", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu

        startChild()
    }

    private func terminateTree() {
        guard let process = child else { return }
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
        child = nil
    }

    private func startChild() {
        if child?.isRunning == true { return }
        let process = Process()
        process.terminationHandler = { _ in
            DispatchQueue.main.async {
                NSApp.terminate(nil)
            }
        }
        do {
            try prepareStablePayload()
            process.executableURL = python
            process.arguments = [
                installRoot.appendingPathComponent("start.py").path,
                "--mode", "observe",
            ]
            process.currentDirectoryURL = installRoot
            try process.run()
            child = process
        } catch {
            let alert = NSAlert()
            alert.messageText = "OpenWorkGraph could not start"
            alert.informativeText = error.localizedDescription
            alert.runModal()
            NSApp.terminate(nil)
        }
    }

    @objc private func restartApp() {
        terminateTree()
        usleep(300_000)
        startChild()
    }

    @objc private func quitApp() {
        terminateTree()
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        terminateTree()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
SWIFT

swiftc -O -framework Cocoa "$SWIFT_SOURCE" -o "$MACOS/OpenWorkGraph"
chmod +x "$MACOS/OpenWorkGraph"
rm -f "$SWIFT_SOURCE"

cat > "$LAUNCH_AGENT" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.kinvectum.openworkgraph</string>
  <key>ProgramArguments</key>
  <array><string>/Applications/OpenWorkGraph.app/Contents/MacOS/OpenWorkGraph</string></array>
  <key>RunAtLoad</key><true/>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
EOF

plutil -lint "$CONTENTS/Info.plist" "$LAUNCH_AGENT"
echo "Built offline macOS app bundle: $APP"
