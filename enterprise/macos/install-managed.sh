#!/bin/bash
set -euo pipefail

: "${OWG_JOIN_CODE:?Set OWG_JOIN_CODE to a personal or single-use organization join code}"
OWG_ACTOR_ID="${OWG_ACTOR_ID:-}"
OWG_EMAIL_DOMAIN="${OWG_EMAIL_DOMAIN:-}"
PKG_URL="${OWG_PKG_URL:-https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.pkg}"
HASH_URL="${OWG_HASH_URL:-https://github.com/KAVentures/openworkgraph/releases/latest/download/OpenWorkGraph-macOS.pkg.sha256}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this Jamf/MDM deployment script as root." >&2
  exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
/usr/bin/curl -fL --retry 3 "$PKG_URL" -o "$TMP/OpenWorkGraph.pkg"
/usr/bin/curl -fL --retry 3 "$HASH_URL" -o "$TMP/OpenWorkGraph.pkg.sha256"
EXPECTED="$(awk '{print $1}' "$TMP/OpenWorkGraph.pkg.sha256" | tr '[:upper:]' '[:lower:]')"
ACTUAL="$(/usr/bin/shasum -a 256 "$TMP/OpenWorkGraph.pkg" | awk '{print $1}')"
[[ -n "$EXPECTED" && "$EXPECTED" == "$ACTUAL" ]] || { echo "OpenWorkGraph package SHA-256 verification failed." >&2; exit 3; }

/usr/sbin/installer -pkg "$TMP/OpenWorkGraph.pkg" -target /

MANAGED_DIR="/Library/Application Support/OpenWorkGraph"
/bin/mkdir -p "$MANAGED_DIR"
/usr/bin/python3 - "$MANAGED_DIR/managed.json" "$OWG_JOIN_CODE" "$OWG_ACTOR_ID" "$OWG_EMAIL_DOMAIN" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
payload = {"join_code": sys.argv[2], "actor_id": sys.argv[3], "email_domain": sys.argv[4]}
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
/bin/chmod 0644 "$MANAGED_DIR/managed.json"
/usr/sbin/chown root:wheel "$MANAGED_DIR/managed.json"

echo "OpenWorkGraph installed in /Applications and managed enrollment staged."
echo "Use a personal/single-use code: the managed file must be readable by the employee app process."
