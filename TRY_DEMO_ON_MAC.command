#!/bin/bash
set -u

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "$SOURCE_DIR/START_ON_MAC.command" --mode demo
