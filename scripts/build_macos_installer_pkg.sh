#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DIST="$ROOT/dist"
APP="$DIST/OpenWorkGraph.app"
PKG_ROOT="$DIST/.pkg-root"
VERSIONED="$DIST/OpenWorkGraph-macOS-v$VERSION.pkg"

if [[ ! -d "$APP" ]]; then
  echo "Expected $APP. Run scripts/build_macos_app_bundle.sh first." >&2
  exit 2
fi

codesign --verify --deep --strict --verbose=2 "$APP"

rm -rf "$PKG_ROOT"
rm -f "$VERSIONED" "$VERSIONED.sha256"
mkdir -p "$PKG_ROOT/Applications"

/usr/bin/ditto "$APP" "$PKG_ROOT/Applications/OpenWorkGraph.app"

pkgbuild \
  --root "$PKG_ROOT" \
  --ownership recommended \
  --identifier "com.kinvectum.openworkgraph.pkg" \
  --version "$VERSION" \
  "$VERSIONED"

pkgutil --payload-files "$VERSIONED" | grep -Eq '^(\./)?Applications/OpenWorkGraph\.app/'
HASH="$(shasum -a 256 "$VERSIONED" | awk '{print $1}')"
printf '%s  %s\n' "$HASH" "$(basename "$VERSIONED")" > "$VERSIONED.sha256"

rm -rf "$PKG_ROOT"
echo "Built macOS installer: $VERSIONED"
