#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DIST="$ROOT/dist"
PKG="$DIST/OpenWorkGraph-macOS-v$VERSION"
STAGE="$DIST/.openworkgraph-payload"
PAYLOAD_ARCHIVE="$DIST/.openworkgraph-payload.tar.gz"
LAUNCHER="$PKG/START_OPENWORKGRAPH.command"

rm -rf "$DIST"
mkdir -p "$PKG" "$STAGE"

# Build the exact source/runtime payload used by START_ON_MAC.command, but embed
# it inside the user-facing launcher. This avoids a second Finder/Gatekeeper hop
# through a hidden sibling directory after the ZIP is downloaded.
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
  "$ROOT/" "$STAGE/"

BUILD_SHA="${GITHUB_SHA:-}"
if [ -z "$BUILD_SHA" ]; then
  BUILD_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
fi
if [ -n "$BUILD_SHA" ]; then
  printf '%s\n' "$BUILD_SHA" > "$STAGE/BUILD_COMMIT"
fi

tar -czf "$PAYLOAD_ARCHIVE" -C "$STAGE" .

cat > "$LAUNCHER" <<'EOF'
#!/bin/bash
set -u

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/openworkgraph-launch.XXXXXX")" || exit 1
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

PAYLOAD="$TMP_ROOT/payload.tar.gz"
if ! awk 'found {print} /^__OPENWORKGRAPH_PAYLOAD_BELOW__$/ {found=1}' "$0" | /usr/bin/base64 -D > "$PAYLOAD"; then
  echo "OpenWorkGraph could not unpack its embedded payload."
  echo
  read -r -p "Press Enter to close…"
  exit 1
fi

if ! tar -xzf "$PAYLOAD" -C "$TMP_ROOT"; then
  echo "OpenWorkGraph could not extract its embedded payload."
  echo
  read -r -p "Press Enter to close…"
  exit 1
fi
rm -f "$PAYLOAD"

chmod +x "$TMP_ROOT/START_ON_MAC.command" 2>/dev/null || true
/bin/bash "$TMP_ROOT/START_ON_MAC.command"
STATUS=$?
exit "$STATUS"

__OPENWORKGRAPH_PAYLOAD_BELOW__
EOF

# macOS /usr/bin/base64 wraps by default. The decoder accepts wrapped input.
/usr/bin/base64 < "$PAYLOAD_ARCHIVE" >> "$LAUNCHER"

cp "$ROOT/ADD_BROWSER_SENSOR.command" "$PKG/ADD_BROWSER_SENSOR.command"
cp "$ROOT/AI_GUIDE.md" "$PKG/AI_GUIDE.md"
cp "$ROOT/PROMPT.md" "$PKG/PROMPT.md"
cp "$ROOT/LICENSE" "$PKG/LICENSE"

cat > "$PKG/README_FIRST.txt" <<EOF
OpenWorkGraph $VERSION — macOS tester build
============================================

1. Unzip this folder.
2. Right-click START_OPENWORKGRAPH.command and choose Open.
3. Confirm Open if macOS asks.
4. On first launch, OpenWorkGraph unpacks its embedded payload, downloads its
   own private runtime, and installs itself under your user Library. You do not
   need to install Python manually.
5. Approve macOS Accessibility/Input Monitoring permissions if requested.
6. The local dashboard opens automatically at http://127.0.0.1:8787.

Browser enrichment (optional)
-----------------------------
OpenWorkGraph works without the browser extension: desktop observation can still
capture browser focus, exposed window/page titles, timing and interactions.

For richer browser-native context, double-click ADD_BROWSER_SENSOR.command after
OpenWorkGraph has started once. It opens the installed browser_extension folder
and your browser extension page. Enable Developer mode, choose Load unpacked, and
select the opened browser_extension folder. The optional sensor adds structured
hostname/path, navigation and safe page/control semantics when available.

The browser sensor is paired to this OpenWorkGraph installation. It verifies the
local server before sending browser evidence. If pairing is lost, use
"Pair / repair browser sensor" in the dashboard and the extension popup. After
upgrading OpenWorkGraph, reload the unpacked extension when the dashboard asks.

AI analysis
-----------
AI_GUIDE.md explains how ChatGPT, Claude and other AI systems should interpret
OpenWorkGraph evidence. PROMPT.md is a short starter instruction you can use when
uploading an export. Exports also carry the guide, prompt and a capture manifest,
so the AI can distinguish captured evidence from OpenWorkGraph's derived heuristic
views and can tell whether browser-extension evidence was present.

Privacy / data location
-----------------------
The prototype runs locally. Captured data stays on this computer unless you
explicitly export it or connect it to another system. Aggregate keyboard activity
is counted, but typed text, individual key identities and clipboard contents are
not captured. Screenshots are disabled in the normal configuration.

High-confidence secrets and sensitive identifiers are hardened before persistence;
presentation/export views additionally pseudonymize detected people/owner aliases.
Useful business context such as company/project names, amounts, order/reference
numbers and page/document titles can remain intentionally, so review rich exports
before sharing them outside the intended analysis context.

Local API/MCP access is capability-protected and browser sensor requests are
paired/signed. These controls reduce accidental localhost exposure and port
squatting, but they are not a boundary against malware already running with the
same macOS user privileges.

License
-------
OpenWorkGraph is open-source software under the Apache License 2.0. The full
license is included as LICENSE in this folder. Copyright 2026 Koyar Afrasyab
(Kinvectum). The software license does not grant rights to project branding.

Stop OpenWorkGraph with Ctrl+C in the Terminal window it opened.

Project: https://github.com/KAVentures/openworkgraph
EOF

chmod +x "$LAUNCHER" "$PKG/ADD_BROWSER_SENSOR.command"
rm -rf "$STAGE" "$PAYLOAD_ARCHIVE"

# ditto is Apple's ZIP tool and preserves the macOS metadata/permissions expected
# for Finder-delivered executables more reliably than a generic zip invocation.
/usr/bin/ditto -c -k --keepParent "$PKG" "$DIST/OpenWorkGraph-macOS.zip"
shasum -a 256 "$DIST/OpenWorkGraph-macOS.zip" > "$DIST/OpenWorkGraph-macOS.zip.sha256"

echo "Built: $DIST/OpenWorkGraph-macOS.zip"
