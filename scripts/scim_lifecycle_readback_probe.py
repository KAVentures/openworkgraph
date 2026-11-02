"""Read-only, consented live SCIM User lifecycle readback from an owned OWG provider.

The operator supplies a bounded JSON manifest on stdin. No SCIM or roster
writes, automatic retries, or customer IdP compatibility claims.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib import request

from .scim_readonly_rollout_probe import (
    SPC, USER, ProbeError, _NoRedirect, _base, _get, _token,
)

FORMAT = "openworkgraph.scim-lifecycle-readback-probe.v1"
MAX_MANIFEST_BYTES = 16384
_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z", re.ASCII)


def _manifest(raw: bytes, confirmed_provider: str) -> list[tuple[str, bool]]:
    if type(raw) is not bytes or len(raw) > MAX_MANIFEST_BYTES:
        raise ProbeError("Bounded manifest required")
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ProbeError("Duplicate manifest key")
                result[key] = value
            return result
        def nonfinite(_):
            raise ProbeError("Invalid manifest number")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ProbeError("Invalid manifest") from None
    if (type(value) is not dict
            or set(value) != {"format", "provider_id", "users"}
            or value["format"] != FORMAT
            or value["provider_id"] != confirmed_provider
            or type(value["users"]) is not list
            or not 1 <= len(value["users"]) <= 8):
        raise ProbeError("Manifest scope refused")
    seen = set()
    result = []
    for entry in value["users"]:
        if (type(entry) is not dict
                or set(entry) != {"id", "expected_active"}
                or type(entry["id"]) is not str
                or _ID.fullmatch(entry["id"]) is None
                or type(entry["expected_active"]) is not bool
                or entry["id"] in seen):
            raise ProbeError("Manifest selection refused")
        seen.add(entry["id"])
        result.append((entry["id"], entry["expected_active"]))
    return result


def lifecycle_readback(base_url, confirm_host, confirm_provider, token, raw_manifest,
                       *, allow_loopback_http=False, opener=None):
    """2+N bounded live GETs; only aggregate, point-in-time receipt is returned."""
    base = _base(base_url, confirm_host, allow_loopback_http)
    if (type(confirm_provider) is not str
            or _ID.fullmatch(confirm_provider) is None
            or base.rsplit("/", 1)[-1] != confirm_provider):
        raise ProbeError("Confirmed provider mismatch")
    selected = _manifest(raw_manifest, confirm_provider)
    bearer = _token(token)
    transport = opener if opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect(),
    )

    def current_provider():
        config = _get(transport, base + "/ServiceProviderConfig", bearer)
        if (config.get("schemas") != [SPC]
                or type(config.get("patch")) is not dict
                or config["patch"].get("supported") is not True):
            raise ProbeError("Current provider authorization unverified")

    current_provider()
    for user_id, expected_active in selected:
        resource = _get(transport, base + "/Users/" + user_id, bearer)
        meta = resource.get("meta")
        if (resource.get("schemas") != [USER]
                or resource.get("id") != user_id
                or type(resource.get("active")) is not bool
                or resource["active"] is not expected_active
                or type(meta) is not dict
                or meta.get("resourceType") != "User"
                or type(meta.get("version")) is not str
                or not 1 <= len(meta["version"]) <= 200):
            raise ProbeError("User lifecycle state unverified")
    current_provider()
    return {
        "format": FORMAT,
        "status": "selected_user_states_verified",
        "users_checked": len(selected),
        "http_gets": len(selected) + 2,
        "provider_writes_performed": False,
        "offboarding_device_sso_revocation": "not_verified",
        "customer_idp_compatibility": "unverified",
    }


def main(argv=None, *, opener=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--confirm-host", required=True)
    parser.add_argument("--confirm-provider", required=True)
    parser.add_argument("--consent-user-state-read", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.consent_user_state_read:
            raise ProbeError("Explicit read consent required")
        stream = stdin if stdin is not None else sys.stdin.buffer
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
        receipt = lifecycle_readback(
            args.base_url, args.confirm_host, args.confirm_provider,
            os.environ.get("OWG_SCIM_PROBE_TOKEN"), raw,
            allow_loopback_http=args.allow_loopback_http, opener=opener,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (ProbeError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused",
            "provider_writes_performed": False,
            "reason": "Verify consent, manifest scope, expected states and current provider authorization",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
