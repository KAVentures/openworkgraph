#!/bin/bash
set -u

MODE="observe"
if [ "${1:-}" = "--mode" ]; then
  MODE="${2:-}"
elif [ "${1:-}" = "--demo" ]; then
  MODE="demo"
fi
if [ "$MODE" != "observe" ] && [ "$MODE" != "demo" ]; then
  echo "Unknown OpenWorkGraph mode: $MODE"
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  SOURCE_DIR="$SCRIPT_DIR"
else
  SOURCE_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi
INSTALL_DIR="$HOME/Library/Application Support/WorkflowObserver"
RUNTIME_DIR="$INSTALL_DIR/.runtime"
LOG_FILE="$HOME/Library/Logs/WorkflowObserver-setup.log"
UV_BIN="$RUNTIME_DIR/bin/uv"
UV_VERSION="0.12.14"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

fail() {
  echo
  echo "Setup did not complete. The log is here:"
  echo "$LOG_FILE"
  echo
  read -r -p "Press Enter to close…"
  exit 1
}
trap fail ERR

mkdir -p "$INSTALL_DIR"

echo "Preparing OpenWorkGraph in your user Library…"
# Copy the prototype out of Downloads/quarantined/translocated locations.
# Preserve local runtime, environment, configuration, and captured data.
rsync -a --delete \
  --exclude '.venv/' \
  --exclude '.runtime/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude 'data/' \
  --exclude 'config.json' \
  "$SOURCE_DIR/" "$INSTALL_DIR/"

# A source checkout keeps implementation helpers organized below apps/,
# integrations/, and platform/. Installed payloads retain the historical root
# filenames for compatibility with shortcuts, older integrations, and upgrades.
if [ "$SCRIPT_DIR" != "$SOURCE_DIR" ]; then
  cp "$SOURCE_DIR/apps/desktop/start.py" "$INSTALL_DIR/start.py"
  cp "$SOURCE_DIR/apps/desktop/config.example.json" "$INSTALL_DIR/config.example.json"
  cp "$SOURCE_DIR/apps/desktop/demo_data.py" "$INSTALL_DIR/demo_data.py"
  cp "$SOURCE_DIR/apps/desktop/windows_tray.py" "$INSTALL_DIR/windows_tray.py"
  cp "$SOURCE_DIR/integrations/agents/owg_connect.py" "$INSTALL_DIR/owg_connect.py"
  cp "$SOURCE_DIR/integrations/agents/owg_bootstrap.sh" "$INSTALL_DIR/owg_bootstrap.sh"
  cp "$SOURCE_DIR/integrations/agents/owg_bootstrap.ps1" "$INSTALL_DIR/owg_bootstrap.ps1"
  cp "$SOURCE_DIR/platform/distribution/launchers/START_ON_MAC.command" "$INSTALL_DIR/START_ON_MAC.command"
  cp "$SOURCE_DIR/platform/distribution/launchers/START_ON_WINDOWS.bat" "$INSTALL_DIR/START_ON_WINDOWS.bat"
  cp "$SOURCE_DIR/platform/distribution/launchers/START_ON_WINDOWS.ps1" "$INSTALL_DIR/START_ON_WINDOWS.ps1"
  cp "$SOURCE_DIR/platform/distribution/launchers/TRY_DEMO_ON_MAC.command" "$INSTALL_DIR/TRY_DEMO_ON_MAC.command"
  cp "$SOURCE_DIR/platform/distribution/launchers/TRY_DEMO_ON_WINDOWS.bat" "$INSTALL_DIR/TRY_DEMO_ON_WINDOWS.bat"
  cp "$SOURCE_DIR/platform/distribution/launchers/ADD_BROWSER_SENSOR.command" "$INSTALL_DIR/ADD_BROWSER_SENSOR.command"
  cp "$SOURCE_DIR/platform/distribution/launchers/ADD_BROWSER_SENSOR_WINDOWS.bat" "$INSTALL_DIR/ADD_BROWSER_SENSOR_WINDOWS.bat"
  cp "$SOURCE_DIR/platform/distribution/installers/install.sh" "$INSTALL_DIR/install.sh"
  cp "$SOURCE_DIR/platform/distribution/installers/install.ps1" "$INSTALL_DIR/install.ps1"
  cp "$SOURCE_DIR/platform/distribution/installers/uninstall.sh" "$INSTALL_DIR/uninstall.sh"
  cp "$SOURCE_DIR/platform/distribution/installers/uninstall.ps1" "$INSTALL_DIR/uninstall.ps1"
fi

cd "$INSTALL_DIR"

export UV_INSTALL_DIR="$RUNTIME_DIR/bin"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
export UV_PYTHON_BIN_DIR="$RUNTIME_DIR/python-bin"
export UV_CACHE_DIR="$RUNTIME_DIR/cache"

if [ ! -x "$UV_BIN" ]; then
  echo "Downloading runtime (first launch can take about 1 minute)…"
  echo "No system Python installation is required."
  mkdir -p "$RUNTIME_DIR/bin"
  INSTALLER="$RUNTIME_DIR/uv-install.sh"
  curl --fail --location --silent --show-error \
    "https://astral.sh/uv/${UV_VERSION}/install.sh" \
    --output "$INSTALLER"
  sh "$INSTALLER"
  rm -f "$INSTALLER"
fi

if [ ! -x .venv/bin/python ]; then
  echo "Downloading Python runtime (first launch can take about 1 minute)…"
  rm -rf .venv
  "$UV_BIN" venv --python 3.12 --managed-python .venv
  echo "Installing OpenWorkGraph dependencies…"
  "$UV_BIN" pip install --python .venv/bin/python -e .
fi

if [ ! -f config.json ]; then
  cp config.example.json config.json
fi

if [ "$MODE" = "demo" ]; then
  echo "Starting OpenWorkGraph demo…"
else
  echo "Starting OpenWorkGraph…"
fi
trap - ERR
exec .venv/bin/python start.py --mode "$MODE"
