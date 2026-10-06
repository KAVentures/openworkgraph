#!/bin/bash
set -euo pipefail

APPLICATIONS_DIR="${OWG_APPLICATIONS_DIR:-$HOME/Applications}"
APP="$APPLICATIONS_DIR/OpenWorkGraph.app"
INSTALL_DIR="$HOME/Library/Application Support/WorkflowObserver"
LOG_FILE="$HOME/Library/Logs/WorkflowObserver-setup.log"

if /usr/bin/lsof -nP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 8787 is currently in use. Quit OpenWorkGraph (or the other service using that port) before uninstalling." >&2
  exit 2
fi

YES=0
if [ "${1:-}" = "--yes" ] || [ "${OWG_UNINSTALL_YES:-0}" = "1" ]; then
  YES=1
fi

if [ "$YES" -ne 1 ]; then
  echo "This will remove OpenWorkGraph, its local runtime, configuration, and recorded local data from:"
  echo "  $INSTALL_DIR"
  echo
  printf "Type DELETE to continue: "
  read -r answer
  if [ "$answer" != "DELETE" ]; then
    echo "Uninstall cancelled."
    exit 0
  fi
fi

rm -rf "$APP"
rm -rf "$INSTALL_DIR"
rm -f "$LOG_FILE"

echo "OpenWorkGraph has been removed from this macOS user account."
echo "Browser extensions are managed by your browser; remove the OpenWorkGraph extension there if you installed it."
