"""Opt-in, read-only selected SCIM Group membership and reciprocal User readback."""
from __future__ import annotations
import argparse
import json
import os
import sys
from urllib import request
from .scim_readonly_rollout_probe import (
    GROUP, SPC, USER, ProbeError, _NoRedirect, _base, _get, _token,
    _unique, _nonfinite,
)
from .scim_lifecycle_readback_probe import _ID

FORMAT = "openworkgraph.scim-group-membership-readback.v1"
MAX_MANIFEST_BYTES = 16384


def _manifest(raw, provider):
    if type(raw) is not bytes or len(raw) > MAX_MANIFEST_BYTES:
        raise ProbeError("Bounded manifest required")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ProbeError("Invalid manifest") from None
    if (type(value) is not dict
            or set(value) != {"format", "provider_id", "users", "groups"}
            or value["format"] != FORMAT or value["provider_id"] != provider
            or type(value["users"]) is not list or not 1 <= len(value["users"]) <= 8
            or type(value["groups"]) is not list or not 1 <= len(value["groups"]) <= 4):
        raise ProbeError("Manifest scope refused")
    users = {}
    for entry in value["users"]:
        if (type(entry) is not dict or set(entry) != {"id", "expected_active"}
                or type(entry["id"]) is not str or _ID.fullmatch(entry["id"]) is None
                or type(entry["expected_active"]) is not bool or entry["id"] in users):
            raise ProbeError("User selection refused")
        users[entry["id"]] = entry["expected_active"]
    groups = {}
    for entry in value["groups"]:
        if (type(entry) is not dict or set(entry) != {"id", "members"}
                or type(entry["id"]) is not str or _ID.fullmatch(entry["id"]) is None
                or not entry["id"].startswith("grp_") or entry["id"] in groups
                or type(entry["members"]) is not list or len(entry["members"]) > 8):
            raise ProbeError("Group selection refused")
        members = entry["members"]
        if (any(type(uid) is not str or uid not in users or not users[uid]
                for uid in members) or len(set(members)) != len(members)):
            raise ProbeError("Contradictory or foreign membership selection")
        groups[entry["id"]] = set(members)
    return users, groups


def _versioned(resource, kind, identifier):
    meta = resource.get("meta")
    if (resource.get("schemas") != [USER if kind == "User" else GROUP]
            or resource.get("id") != identifier or type(meta) is not dict
            or meta.get("resourceType") != kind
            or type(meta.get("version")) is not str
            or not 1 <= len(meta["version"]) <= 200):
        raise ProbeError("Resource identity/version refused")


def _provider(transport, base, bearer):
    config = _get(transport, base + "/ServiceProviderConfig", bearer)
    if (config.get("schemas") != [SPC] or type(config.get("patch")) is not dict
            or config["patch"].get("supported") is not True):
        raise ProbeError("Provider authorization unverified")


def membership_readback(base_url, confirm_host, confirm_provider, token, raw_manifest,
                        *, allow_loopback_http=False, opener=None):
    """4–14 GETs; exact selected Group sets and reciprocal User projections."""
    base = _base(base_url, confirm_host, allow_loopback_http)
    if (type(confirm_provider) is not str or _ID.fullmatch(confirm_provider) is None
            or base.rsplit("/", 1)[-1] != confirm_provider):
        raise ProbeError("Confirmed provider mismatch")
    users, groups = _manifest(raw_manifest, confirm_provider)
    bearer = _token(token)
    transport = opener if opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())
    _provider(transport, base, bearer)
    for gid, expected in groups.items():
        group = _get(transport, base + "/Groups/" + gid, bearer)
        _versioned(group, "Group", gid)
        members = group.get("members")
        if type(members) is not list or len(members) > 200:
            raise ProbeError("Group member projection refused")
        observed = set()
        for member in members:
            if (type(member) is not dict or set(member) != {"value", "type"}
                    or member.get("type") != "User"
                    or type(member.get("value")) is not str
                    or _ID.fullmatch(member["value"]) is None
                    or member["value"] in observed):
                raise ProbeError("Invalid direct member projection")
            observed.add(member["value"])
        if observed != expected:
            raise ProbeError("Selected Group membership mismatch")
    for uid, active in users.items():
        user = _get(transport, base + "/Users/" + uid, bearer)
        _versioned(user, "User", uid)
        if type(user.get("active")) is not bool or user["active"] is not active:
            raise ProbeError("Selected User lifecycle mismatch")
        projections = user.get("groups", [])
        if type(projections) is not list or len(projections) > 100:
            raise ProbeError("User Group projection refused")
        seen = set()
        for entry in projections:
            if (type(entry) is not dict
                    or set(entry) != {"value", "display", "type"}
                    or type(entry.get("value")) is not str
                    or _ID.fullmatch(entry["value"]) is None
                    or type(entry.get("display")) is not str
                    or entry.get("type") != "direct" or entry["value"] in seen):
                raise ProbeError("Invalid reciprocal Group projection")
            seen.add(entry["value"])
        expected = {gid for gid, members in groups.items() if uid in members}
        if seen.intersection(groups) != expected:
            raise ProbeError("Reciprocal Group membership mismatch")
    _provider(transport, base, bearer)
    return {"format": FORMAT, "status": "selected_group_membership_verified",
            "groups_checked": len(groups), "users_checked": len(users),
            "http_gets": len(groups) + len(users) + 2,
            "provider_writes_performed": False,
            "owg_roles_teams_sso_permissions": "not_verified",
            "customer_idp_compatibility": "unverified"}


def main(argv=None, *, opener=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--confirm-host", required=True)
    parser.add_argument("--confirm-provider", required=True)
    parser.add_argument("--consent-membership-read", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.consent_membership_read:
            raise ProbeError("Explicit Group/User read consent required")
        stream = stdin if stdin is not None else sys.stdin.buffer
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
        result = membership_readback(
            args.base_url, args.confirm_host, args.confirm_provider,
            os.environ.get("OWG_SCIM_PROBE_TOKEN"), raw,
            allow_loopback_http=args.allow_loopback_http, opener=opener)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ProbeError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused", "provider_writes_performed": False,
            "reason": "Verify consent, selected membership, lifecycle and current provider authorization",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
