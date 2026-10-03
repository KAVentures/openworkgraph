#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
APP="$ROOT/dist/OpenWorkGraph.app"
LAUNCH_AGENT="$ROOT/dist/com.kinvectum.openworkgraph.plist"
PKG_ROOT="$ROOT/dist/.signed-pkg-root"
UNSIGNED="$ROOT/dist/OpenWorkGraph-macOS-v$VERSION-unsigned.pkg"
SIGNED="$ROOT/dist/OpenWorkGraph-macOS-v$VERSION.pkg"
APP_ZIP="$ROOT/dist/OpenWorkGraph-macOS-v$VERSION-app-notary.zip"

: "${APPLE_APPLICATION_IDENTITY:?APPLE_APPLICATION_IDENTITY is required}"
: "${APPLE_INSTALLER_IDENTITY:?APPLE_INSTALLER_IDENTITY is required}"
: "${APPLE_ID:?APPLE_ID is required for notarization}"
: "${APPLE_APP_PASSWORD:?APPLE_APP_PASSWORD is required for notarization}"
: "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required for notarization}"

if [[ ! -d "$APP" || ! -f "$LAUNCH_AGENT" ]]; then
  echo "Expected offline app bundle and LaunchAgent. Run scripts/build_macos_app_bundle.sh first." >&2
  exit 2
fi

rm -rf "$PKG_ROOT"
rm -f "$UNSIGNED" "$SIGNED" "$SIGNED.sha256" "$APP_ZIP"

# Sign embedded Mach-O code first, then the stable application bundle. The
# embedded Python interpreter/dependencies therefore carry a Developer ID code
# requirement instead of changing identity on each tester extraction.
while IFS= read -r -d '' file_path; do
  if /usr/bin/file -b "$file_path" | grep -q 'Mach-O'; then
    codesign --force --options runtime --timestamp       --sign "$APPLE_APPLICATION_IDENTITY" "$file_path"
  fi
done < <(find "$APP/Contents/Resources/openworkgraph/.venv" -type f -print0)

codesign --force --options runtime --timestamp --deep   --sign "$APPLE_APPLICATION_IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

# Notarize and staple the .app itself so direct Gatekeeper assessment after
# installation does not depend only on the outer installer ticket.
/usr/bin/ditto -c -k --keepParent "$APP" "$APP_ZIP"
xcrun notarytool submit "$APP_ZIP"   --apple-id "$APPLE_ID"   --password "$APPLE_APP_PASSWORD"   --team-id "$APPLE_TEAM_ID"   --wait
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute --verbose=4 "$APP"
rm -f "$APP_ZIP"

mkdir -p "$PKG_ROOT/Applications" "$PKG_ROOT/Library/LaunchAgents"
/usr/bin/ditto "$APP" "$PKG_ROOT/Applications/OpenWorkGraph.app"
cp "$LAUNCH_AGENT" "$PKG_ROOT/Library/LaunchAgents/com.kinvectum.openworkgraph.plist"

pkgbuild   --root "$PKG_ROOT"   --ownership recommended   --identifier "com.kinvectum.openworkgraph.pkg"   --version "$VERSION"   "$UNSIGNED"

productsign --sign "$APPLE_INSTALLER_IDENTITY" "$UNSIGNED" "$SIGNED"
rm -f "$UNSIGNED"
pkgutil --check-signature "$SIGNED"

xcrun notarytool submit "$SIGNED"   --apple-id "$APPLE_ID"   --password "$APPLE_APP_PASSWORD"   --team-id "$APPLE_TEAM_ID"   --wait
xcrun stapler staple "$SIGNED"
xcrun stapler validate "$SIGNED"
spctl --assess --type install --verbose=4 "$SIGNED"

HASH="$(shasum -a 256 "$SIGNED" | awk '{print $1}')"
printf '%s  %s\n' "$HASH" "$(basename "$SIGNED")" > "$SIGNED.sha256"
rm -rf "$PKG_ROOT"
echo "Built signed/notarized offline app installer: $SIGNED"
