#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
PACKAGE_DIR="$ROOT/dist/OpenWorkGraph-macOS-v$VERSION"
UNSIGNED="$ROOT/dist/OpenWorkGraph-macOS-v$VERSION-unsigned.pkg"
SIGNED="$ROOT/dist/OpenWorkGraph-macOS-v$VERSION.pkg"

: "${APPLE_INSTALLER_IDENTITY:?APPLE_INSTALLER_IDENTITY is required}"
: "${APPLE_ID:?APPLE_ID is required for notarization}"
: "${APPLE_APP_PASSWORD:?APPLE_APP_PASSWORD is required for notarization}"
: "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required for notarization}"

if [[ ! -d "$PACKAGE_DIR" ]]; then
  echo "Expected $PACKAGE_DIR. Run scripts/build_macos_release.sh first." >&2
  exit 2
fi

rm -f "$UNSIGNED" "$SIGNED" "$SIGNED.sha256"

pkgbuild \
  --root "$PACKAGE_DIR" \
  --install-location "/Applications/OpenWorkGraph" \
  --identifier "com.kinvectum.openworkgraph" \
  --version "$VERSION" \
  "$UNSIGNED"

productsign --sign "$APPLE_INSTALLER_IDENTITY" "$UNSIGNED" "$SIGNED"
rm -f "$UNSIGNED"

pkgutil --check-signature "$SIGNED"

xcrun notarytool submit "$SIGNED" \
  --apple-id "$APPLE_ID" \
  --password "$APPLE_APP_PASSWORD" \
  --team-id "$APPLE_TEAM_ID" \
  --wait

xcrun stapler staple "$SIGNED"
xcrun stapler validate "$SIGNED"
spctl --assess --type install --verbose=4 "$SIGNED"

HASH="$(shasum -a 256 "$SIGNED" | awk '{print $1}')"
printf '%s  %s\n' "$HASH" "$(basename "$SIGNED")" > "$SIGNED.sha256"
echo "Built signed/notarized installer: $SIGNED"
