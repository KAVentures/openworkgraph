#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_ROOT="$HOME/Library/Application Support/WorkflowObserver"
LOG="/tmp/openworkgraph-agent-bootstrap-installer.log"

find_python() {
  if [ -x "$INSTALL_ROOT/.venv/bin/python" ]; then
    printf '%s\n' "$INSTALL_ROOT/.venv/bin/python"
    return 0
  fi
  for app in "/Applications/OpenWorkGraph.app" "$HOME/Applications/OpenWorkGraph.app"; do
    payload="$app/Contents/Resources/openworkgraph"
    marker="$payload/EMBEDDED_PYTHON.txt"
    if [ -f "$marker" ]; then
      rel="$(cat "$marker" 2>/dev/null || true)"
      if [ -n "$rel" ] && [ -x "$payload/$rel" ]; then
        printf '%s\n' "$payload/$rel"
        return 0
      fi
    fi
  done
  return 1
}

health_ok() {
  /usr/bin/curl -fsS --max-time 2 http://127.0.0.1:8787/health 2>/dev/null \
    | /usr/bin/grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'
}

PYTHON="$(find_python || true)"
LAUNCHED=0
INSTALL_PID=""

if [ -z "$PYTHON" ]; then
  /bin/bash "$ROOT/install.sh" >"$LOG" 2>&1 &
  INSTALL_PID=$!
  LAUNCHED=1

  for _ in $(seq 1 240); do
    PYTHON="$(find_python || true)"
    [ -n "$PYTHON" ] && break
    if ! kill -0 "$INSTALL_PID" 2>/dev/null; then
      echo "OpenWorkGraph installation stopped before its private runtime was ready." >&2
      echo "Installer log: $LOG" >&2
      exit 1
    fi
    sleep 1
  done
fi

if [ -z "$PYTHON" ]; then
  echo "OpenWorkGraph private runtime was not provisioned." >&2
  echo "Installer log: $LOG" >&2
  exit 1
fi

if [ "$LAUNCHED" = "1" ]; then
  for _ in $(seq 1 240); do
    health_ok && break
    if ! kill -0 "$INSTALL_PID" 2>/dev/null; then
      echo "OpenWorkGraph installer exited before the local service became healthy." >&2
      echo "Installer log: $LOG" >&2
      exit 1
    fi
    sleep 1
  done
  if ! health_ok; then
    echo "OpenWorkGraph did not become healthy after installation." >&2
    echo "Installer log: $LOG" >&2
    exit 1
  fi
fi

export OWG_INSTALLED_ROOT="$INSTALL_ROOT"
export OWG_INSTALLED_PYTHON="$PYTHON"
exec "$PYTHON" "$ROOT/owg_connect.py" bootstrap --local "$@"
