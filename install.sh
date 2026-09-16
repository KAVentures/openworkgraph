#!/bin/bash
set -euo pipefail

URL="https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.zip"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

ZIP="$TMP/OpenWorkGraph-macOS.zip"
echo "Downloading the latest OpenWorkGraph macOS tester build..."
curl -fL --retry 3 --retry-delay 1 "$URL" -o "$ZIP"
unzip -q "$ZIP" -d "$TMP/unpacked"
START="$(find "$TMP/unpacked" -maxdepth 2 -name START_OPENWORKGRAPH.command -type f | head -n 1)"
if [ -z "$START" ]; then
  echo "Could not find START_OPENWORKGRAPH.command in the release package." >&2
  exit 1
fi
chmod +x "$START"
echo "Starting OpenWorkGraph..."
/bin/bash "$START"
