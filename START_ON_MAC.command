#!/bin/bash
set -u

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
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

echo "Preparing Workflow Observer in your user Library…"
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

cd "$INSTALL_DIR"

export UV_INSTALL_DIR="$RUNTIME_DIR/bin"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
export UV_PYTHON_BIN_DIR="$RUNTIME_DIR/python-bin"
export UV_CACHE_DIR="$RUNTIME_DIR/cache"

if [ ! -x "$UV_BIN" ]; then
  echo "First-time setup: installing a private app runtime…"
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
  echo "First-time setup: downloading Workflow Observer's private Python runtime…"
  rm -rf .venv
  "$UV_BIN" venv --python 3.12 --managed-python .venv
  echo "Installing Workflow Observer dependencies…"
  "$UV_BIN" pip install --python .venv/bin/python -e .
fi

if [ ! -f config.json ]; then
  cp config.example.json config.json
fi

echo "Starting Workflow Observer…"
trap - ERR
exec .venv/bin/python start.py --mode observe
