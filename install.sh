#!/bin/bash
set -euo pipefail

RELEASE_VERSION="0.122.0"
URL="${OWG_INSTALL_URL:-https://github.com/KAVentures/openworkgraph/releases/download/v${RELEASE_VERSION}/OpenWorkGraph-macOS.zip}"
MODE="${OWG_INSTALL_MODE:-observe}"
if [ "$MODE" != "observe" ] && [ "$MODE" != "demo" ]; then
  echo "OWG_INSTALL_MODE must be observe or demo." >&2
  exit 2
fi

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

ZIP="$TMP/OpenWorkGraph-macOS.zip"
echo "Downloading OpenWorkGraph v$RELEASE_VERSION for macOS..."
if [ -n "${OWG_INSTALL_ZIP_PATH:-}" ]; then
  cp "$OWG_INSTALL_ZIP_PATH" "$ZIP"
else
  curl -fL --retry 3 --retry-delay 1 "$URL" -o "$ZIP"
fi
unzip -q "$ZIP" -d "$TMP/unpacked"

if [ "$MODE" = "demo" ]; then
  START="$(find "$TMP/unpacked" -maxdepth 2 -name TRY_DEMO_OPENWORKGRAPH.command -type f | head -n 1)"
else
  START="$(find "$TMP/unpacked" -maxdepth 2 -name START_OPENWORKGRAPH.command -type f | head -n 1)"
fi
if [ -z "$START" ]; then
  echo "Could not find the OpenWorkGraph launcher in the release package." >&2
  exit 1
fi
chmod +x "$START"

APP_CREATED=0
APP_EXISTED=0

# The one-command installer is only a bootstrap. The release launcher remains
# the source of truth for installation, upgrades, runtime setup, data
# preservation, permissions and startup behavior.
install_finder_launcher() {
  local applications_dir="${OWG_APPLICATIONS_DIR:-$HOME/Applications}"
  local app="$applications_dir/OpenWorkGraph.app"
  local tmp_app="$applications_dir/.OpenWorkGraph.app.tmp.$$"
  local executable="$tmp_app/Contents/MacOS/OpenWorkGraph"

  if [ -d "$app" ]; then
    APP_EXISTED=1
  fi

  rm -rf "$tmp_app"
  mkdir -p "$tmp_app/Contents/MacOS"

  cat > "$tmp_app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key><string>OpenWorkGraph</string>
  <key>CFBundleExecutable</key><string>OpenWorkGraph</string>
  <key>CFBundleIdentifier</key><string>com.kinvectum.openworkgraph.bootstrap</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>OpenWorkGraph</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><false/>
</dict>
</plist>
PLIST

  cat > "$executable" <<'LAUNCHER'
#!/bin/bash
set -euo pipefail
LAUNCHER="$HOME/Library/Application Support/WorkflowObserver/START_ON_MAC.command"
if [ ! -f "$LAUNCHER" ]; then
  /usr/bin/osascript -e 'display alert "OpenWorkGraph is not installed yet" message "Run the OpenWorkGraph installer once, then open OpenWorkGraph again." as critical'
  exit 1
fi
chmod +x "$LAUNCHER" 2>/dev/null || true

# CI-only path: exercise this exact application launcher without requiring a
# graphical Terminal window on the GitHub-hosted macOS runner.
if [ "${OWG_SHORTCUT_TEST_DIRECT:-0}" = "1" ]; then
  if [ "${OWG_INSTALL_MODE:-observe}" = "demo" ]; then
    exec /bin/bash "$LAUNCHER" --mode demo
  fi
  exec /bin/bash "$LAUNCHER"
fi

exec /usr/bin/open -a Terminal "$LAUNCHER"
LAUNCHER
  chmod +x "$executable"

  rm -rf "$app"
  mv "$tmp_app" "$app"
  APP_CREATED=1
  echo "OpenWorkGraph launcher installed in $applications_dir."
}

if [ "${OWG_INSTALL_NO_SHORTCUT:-0}" != "1" ]; then
  install_finder_launcher
fi

echo "Starting OpenWorkGraph..."
echo "After this first setup, you can reopen it from Applications as OpenWorkGraph."
set +e
/bin/bash "$START"
STATUS=$?
set -e

# Do not leave a brand-new convenience launcher behind after a failed first
# install. Existing launchers from an earlier working install are preserved.
if [ "$STATUS" -ne 0 ] && [ "$APP_CREATED" -eq 1 ] && [ "$APP_EXISTED" -eq 0 ]; then
  rm -rf "${OWG_APPLICATIONS_DIR:-$HOME/Applications}/OpenWorkGraph.app"
fi
exit "$STATUS"
