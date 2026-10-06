from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def get_json(url: str, token: str = "") -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check an OpenWorkGraph Gateway before/after fleet rollout")
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--token", default="", help="Optional scoped/admin token for /v1/capabilities")
    args = parser.parse_args()
    base = args.gateway.rstrip("/")
    checks = [("/health", False), ("/v1/capabilities", bool(args.token))]
    failed = False
    for path, needs_token in checks:
        if needs_token and not args.token:
            print(f"SKIP {path}: no token supplied")
            continue
        try:
            payload = get_json(base + path, args.token if needs_token else "")
            print(f"OK   {path}: {json.dumps(payload, sort_keys=True)[:500]}")
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            failed = True
            print(f"FAIL {path}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
