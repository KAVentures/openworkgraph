#!/bin/bash
set -euo pipefail

# Remove managed client integration BEFORE deleting any interpreter/script that
# Claude Code, Claude Desktop, Codex etc. might still execute after uninstall.
INSTALL_DIR="${OWG_UNINSTALL_INSTALL_DIR:-$HOME/Library/Application Support/WorkflowObserver}"
USER_APPLICATIONS="${OWG_APPLICATIONS_DIR:-$HOME/Applications}"
SYSTEM_APPLICATIONS="${OWG_SYSTEM_APPLICATIONS_DIR:-/Applications}"
LOG_FILE="$HOME/Library/Logs/WorkflowObserver-setup.log"
YES=0
if [ "${1:-}" = "--yes" ] || [ "${OWG_UNINSTALL_YES:-0}" = "1" ]; then YES=1; fi

if [ "$YES" -ne 1 ]; then
  echo "This removes OpenWorkGraph, its app, runtime, all local history/data,"
  echo "and OpenWorkGraph-managed MCP connections and hooks from your AI apps."
  echo "It preserves unrelated AI app settings and hooks."
  printf 'Type DELETE to continue: '
  read -r answer
  if [ "$answer" != "DELETE" ]; then
    echo "Uninstall cancelled."
    exit 0
  fi
fi

# Never mistake an unrelated process on 8787 for an OWG process and kill it.
LSOF=/usr/sbin/lsof
if [ ! -x "$LSOF" ]; then LSOF=/usr/bin/lsof; fi
if [ -x "$LSOF" ] && "$LSOF" -nP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 8787 is in use. Quit OpenWorkGraph before uninstalling." >&2
  exit 2
fi

CONNECT="$INSTALL_DIR/owg_connect.py"
if [ -f "$CONNECT" ]; then
  PYTHON="${OWG_UNINSTALL_PYTHON:-}"
  if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    # Prefer a Python shipped with this installation. On a clean signed Mac,
    # /usr/bin/python3 may exist but cannot import OWG's bundled dependencies.
    for candidate in "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/.runtime/bin/python"; do
      if [ -x "$candidate" ]; then PYTHON="$candidate"; break; fi
    done
  fi
  if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    # Signed offline bundles keep their actual Python under
    # Contents/Resources/openworkgraph/.runtime, not ~/.venv.
    for bundle in "$SYSTEM_APPLICATIONS/OpenWorkGraph.app" "$USER_APPLICATIONS/OpenWorkGraph.app"; do
      marker="$bundle/Contents/Resources/openworkgraph/EMBEDDED_PYTHON.txt"
      if [ -f "$marker" ]; then
        relative="$(head -n 1 "$marker")"
        case "$relative" in
          .runtime/*) candidate="$bundle/Contents/Resources/openworkgraph/$relative" ;;
          *) candidate="" ;;
        esac
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
          PYTHON="$candidate"
          break
        fi
      fi
    done
  fi
  # Compatibility fallback for older source installs without an embedded
  # interpreter. Use it only after examining every bundled runtime.
  if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    if [ -x /usr/bin/python3 ]; then PYTHON=/usr/bin/python3; fi
  fi
  if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    echo "Cannot clean up AI clients: Python unavailable. Runtime preserved." >&2
    exit 1
  fi
  echo "Removing only OpenWorkGraph-managed AI integrations..."
  # Each removal handles MCP and observation/hooks; stale entries in clients
  # must never remain pointing at a deleted runtime. Fail closed on error.
  for client in claude_code claude_desktop codex cursor vscode windsurf gemini_cli copilot_cli kiro amazon_q; do
    if ! "$PYTHON" "$CONNECT" remove "$client" >/dev/null; then
      echo "Could not remove the $client integration; runtime preserved." >&2
      exit 1
    fi
  done
fi

# Remove only OWG-owned app bundles, not a different app someone put at the
# same path. Signed installers target /Applications, bootstrap targets ~/Applications.
for dir in "$USER_APPLICATIONS" "$SYSTEM_APPLICATIONS"; do
  app="$dir/OpenWorkGraph.app"
  if [ ! -d "$app" ]; then continue; fi
  plist="$app/Contents/Info.plist"
  identifier=""
  if [ -f "$plist" ] && [ -x /usr/libexec/PlistBuddy ]; then
    identifier="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist" 2>/dev/null || true)"
  fi
  if [ "$identifier" != "com.kinvectum.openworkgraph" ] && [ "$identifier" != "com.kinvectum.openworkgraph.bootstrap" ]; then
    echo "Skipping unexpected application bundle at $app (identifier: $identifier)." >&2
    continue
  fi
  if ! /bin/rm -rf "$app" 2>/dev/null; then
    # A signed .pkg installs /Applications/OpenWorkGraph.app as root-owned.
    # Elevate only for this verified, exact bundle path, from an interactive
    # Terminal. Do not elevate to edit AI app settings or user evidence.
    if [ -t 0 ] && [ "$app" = "$SYSTEM_APPLICATIONS/OpenWorkGraph.app" ] && [ -x /usr/bin/sudo ]; then
      echo "Administrator permission is required to remove the signed app at $app."
      if ! /usr/bin/sudo /bin/rm -rf "$app"; then
        echo "App removal cancelled. The local runtime was preserved." >&2
        exit 1
      fi
    else
      echo "Unable to remove $app (administrator permission may be needed)." >&2
      echo "Remove the app manually, then rerun this uninstaller." >&2
      exit 1
    fi
  fi
done

rm -rf "$INSTALL_DIR"
rm -f "$LOG_FILE"
echo "OpenWorkGraph integrations, runtime and local data removed."
echo "Remove the optional OpenWorkGraph browser extension from your browser's extensions page."
