#!/bin/bash
set -euo pipefail

URL="${OWG_INSTALL_URL:-https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.zip}"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

ZIP="$TMP/OpenWorkGraph-macOS.zip"
echo "Downloading the latest OpenWorkGraph macOS build..."
if [ -n "${OWG_INSTALL_ZIP_PATH:-}" ]; then
  cp "$OWG_INSTALL_ZIP_PATH" "$ZIP"
else
  curl -fL --retry 3 --retry-delay 1 "$URL" -o "$ZIP"
fi
unzip -q "$ZIP" -d "$TMP/unpacked"

START="$(find "$TMP/unpacked" -maxdepth 2 -name START_OPENWORKGRAPH.command -type f | head -n 1)"
if [ -z "$START" ]; then
  echo "Could not find START_OPENWORKGRAPH.command in the release package." >&2
  exit 1
fi
chmod +x "$START"

# The one-command installer is only a bootstrap. The release launcher remains
# the source of truth for installation, upgrades, runtime setup, data
# preservation, permissions and startup behavior.
install_finder_launcher() {
  local applications_dir="${OWG_APPLICATIONS_DIR:-$HOME/Applications}"
  local app="$applications_dir/OpenWorkGraph.app"
  local tmp_app="$applications_dir/.OpenWorkGraph.app.tmp.$$"
  local executable="$tmp_app/Contents/MacOS/OpenWorkGraph"

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
  /usr/bin/osascript -e 'display alert "OpenWorkGraph is not installed yet" message "Run the OpenWorkGraph installer command once, then open OpenWorkGraph again." as critical'
  exit 1
fi
chmod +x "$LAUNCHER" 2>/dev/null || true
exec /usr/bin/open -a Terminal "$LAUNCHER"
LAUNCHER
  chmod +x "$executable"

  rm -rf "$app"
  mv "$tmp_app" "$app"
  echo "OpenWorkGraph launcher installed in $applications_dir."
}

if [ "${OWG_INSTALL_NO_SHORTCUT:-0}" != "1" ]; then
  install_finder_launcher
fi

echo "Starting OpenWorkGraph..."
echo "After this first setup, you can reopen it from Applications as OpenWorkGraph."
/bin/bash "$START"
