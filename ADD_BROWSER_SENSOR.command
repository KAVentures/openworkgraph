#!/bin/bash
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/Library/Application Support/WorkflowObserver"

if [ -d "$INSTALL_DIR/browser_extension" ]; then
  SENSOR_DIR="$INSTALL_DIR/browser_extension"
elif [ -d "$ROOT/.openworkgraph-src/browser_extension" ]; then
  SENSOR_DIR="$ROOT/.openworkgraph-src/browser_extension"
elif [ -d "$ROOT/browser_extension" ]; then
  SENSOR_DIR="$ROOT/browser_extension"
else
  echo "Could not find the OpenWorkGraph browser sensor folder."
  echo "Start OpenWorkGraph once, then run this helper again."
  echo
  read -r -p "Press Enter to close…"
  exit 1
fi

echo
echo "OpenWorkGraph browser sensor"
echo "============================"
echo
echo "A Finder window will open with the browser_extension folder."
echo "In Chrome or Edge:"
echo "  1. Open the Extensions page."
echo "  2. Turn on Developer mode."
echo "  3. Click Load unpacked."
echo "  4. Select the browser_extension folder that Finder just opened."
echo
echo "This browser sensor is needed so OpenWorkGraph can distinguish Gmail,"
echo "Google Docs, Salesforce and other browser work instead of seeing only Chrome."
echo

open "$SENSOR_DIR"

if [ -d "/Applications/Google Chrome.app" ]; then
  open -a "Google Chrome" "chrome://extensions" || true
elif [ -d "/Applications/Microsoft Edge.app" ]; then
  open -a "Microsoft Edge" "edge://extensions" || true
fi

echo "Folder: $SENSOR_DIR"
echo
read -r -p "Press Enter when finished…"
