#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DIST="$ROOT/dist"
PKG="$DIST/OpenWorkGraph-macOS-v$VERSION"
PAYLOAD="$PKG/.openworkgraph-src"

rm -rf "$DIST"
mkdir -p "$PAYLOAD"

# Keep the user-facing package simple while shipping the exact source/runtime
# payload the launcher needs. Hidden payload files remain inspectable if a
# tester wants to audit them.
rsync -a \
  --exclude '.git/' \
  --exclude '.github/' \
  --exclude '.venv/' \
  --exclude '.runtime/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude 'data/' \
  --exclude 'dist/' \
  --exclude 'tests/' \
  --exclude 'scripts/' \
  --exclude 'config.json' \
  "$ROOT/" "$PAYLOAD/"

cat > "$PKG/START_OPENWORKGRAPH.command" <<'EOF'
#!/bin/bash
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "$ROOT/.openworkgraph-src/START_ON_MAC.command"
EOF

cp "$ROOT/ADD_BROWSER_SENSOR.command" "$PKG/ADD_BROWSER_SENSOR.command"

cat > "$PKG/README_FIRST.txt" <<EOF
OpenWorkGraph $VERSION — macOS tester build
============================================

1. Unzip this folder.
2. Right-click START_OPENWORKGRAPH.command and choose Open.
3. Confirm Open if macOS asks.
4. On first launch, OpenWorkGraph downloads its own private runtime and installs
   itself under your user Library. You do not need to install Python manually.
5. Approve macOS Accessibility/Input Monitoring permissions if requested.
6. The local dashboard opens automatically at http://127.0.0.1:8787.

Browser context (recommended)
-----------------------------
Double-click ADD_BROWSER_SENSOR.command after OpenWorkGraph has started once.
It opens the correct extension folder and your browser extension page. Enable
Developer mode, choose Load unpacked, and select the opened browser_extension
folder. This lets OpenWorkGraph distinguish Gmail, Docs, Salesforce and other
browser work instead of seeing only the browser application.

Privacy / data location
-----------------------
The prototype runs locally. Captured data stays on this computer unless you
explicitly export it. Aggregate keyboard activity is counted, but typed text and
key identities are not recorded by the effort counter.

Stop OpenWorkGraph with Ctrl+C in the Terminal window it opened.

Project: https://github.com/KAVentures/openworkgraph
EOF

chmod +x "$PKG/START_OPENWORKGRAPH.command" "$PKG/ADD_BROWSER_SENSOR.command"

# Stable asset name makes the README download link remain valid across releases.
cd "$DIST"
zip -qry "OpenWorkGraph-macOS.zip" "$(basename "$PKG")"
shasum -a 256 "OpenWorkGraph-macOS.zip" > "OpenWorkGraph-macOS.zip.sha256"

echo "Built: $DIST/OpenWorkGraph-macOS.zip"
