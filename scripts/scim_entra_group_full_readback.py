"""Bounded live Entra -> OWG SCIM *complete direct-user* Group readback.

This is a read-only operator check of selected Groups, not an authorization,
identity-adoption, nested-group, or atomic-directory-snapshot guarantee.
"""
from __future__ import annotations

from server.connector_oauth import connector_token as _connector_token
import argparse
import json
import os
import sys
from urllib import error, request

from .scim_entra_lifecycle_reconcile import (
    _graph_origin, _graph_token, _graph_user, _guid, _scim_authority,
)
from .scim_entra_group_reconcile import _owg_group, _owg_user
from .scim_lifecycle_readback_probe import _ID
from .scim_readonly_rollout_probe import (
    ProbeError, _NoRedirect, _base, _nonfinite, _token, _unique,
)

FORMAT = "openworkgraph.scim-entra-group-full-readback.v1"
MAX_MANIFEST_BYTES = 16384
MAX_GRAPH_BYTES = 65536
MAX_USERS = 8
MAX_GROUPS = 4


def _manifest(raw, provider, tenant):
    if type(raw) is not bytes or len(raw) > MAX_MANIFEST_BYTES:
        raise ProbeError("Bounded private selection required")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProbeError("Invalid private selection") from None
    if (type(data) is not dict
            or set(data) != {"format", "provider_id", "entra_tenant_id", "users", "groups"}
            or data["format"] != FORMAT or data["provider_id"] != provider
            or _guid(data["entra_tenant_id"]) != tenant
            or type(data["users"]) is not list
            or not 1 <= len(data["users"]) <= MAX_USERS
            or type(data["groups"]) is not list
            or not 1 <= len(data["groups"]) <= MAX_GROUPS):
        raise ProbeError("Selection scope mismatch")
    users, owg_user_ids = {}, set()
    for entry in data["users"]:
        if (type(entry) is not dict
                or set(entry) != {"entra_id", "owg_id", "expected_active"}
                or type(entry["owg_id"]) is not str
                or _ID.fullmatch(entry["owg_id"]) is None
                or type(entry["expected_active"]) is not bool):
            raise ProbeError("Invalid selected user mapping")
        eid = _guid(entry["entra_id"])
        oid = entry["owg_id"]
        if eid in users or oid in owg_user_ids:
            raise ProbeError("Duplicate user mapping")
        users[eid] = (oid, entry["expected_active"])
        owg_user_ids.add(oid)

    groups, owg_group_ids = {}, set()
    for entry in data["groups"]:
        if (type(entry) is not dict
                or set(entry) != {"entra_id", "owg_id", "member_entra_ids"}
                or type(entry["owg_id"]) is not str
                or _ID.fullmatch(entry["owg_id"]) is None
                or not entry["owg_id"].startswith("grp_")
                or type(entry["member_entra_ids"]) is not list
                or len(entry["member_entra_ids"]) > MAX_USERS):
            raise ProbeError("Invalid selected group mapping")
        eid = _guid(entry["entra_id"])
        oid = entry["owg_id"]
        if eid in groups or oid in owg_group_ids:
            raise ProbeError("Duplicate group mapping")
        members = [_guid(v) for v in entry["member_entra_ids"]]
        if (len(set(members)) != len(members)
                or any(uid not in users or not users[uid][1] for uid in members)):
            raise ProbeError("Contradictory direct membership")
        groups[eid] = (oid, set(members))
        owg_group_ids.add(oid)
    return users, groups


def _graph_group_users(opener, origin, bearer, group_id):
    """Read one *complete* Graph direct-user page or refuse, never follow links."""
    url = (origin + "/v1.0/groups/" + group_id
           + "/members/microsoft.graph.user?%24select=id&%24top=100&%24count=true")
    req = request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + bearer,
        "Accept": "application/json",
        "ConsistencyLevel": "eventual",
        "Cache-Control": "no-store",
    })
    try:
        with opener.open(req, timeout=10) as response:
            status = response.status
            final = response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
            raw = response.read(MAX_GRAPH_BYTES + 1)
    except (OSError, error.URLError, ValueError, ProbeError):
        raise ProbeError("Microsoft Graph direct-user Group read refused") from None
    if (status != 200 or final != url or media != "application/json"
            or len(raw) > MAX_GRAPH_BYTES):
        raise ProbeError("Microsoft Graph Group response refused")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProbeError("Invalid Microsoft Graph Group response") from None
    if (type(data) is not dict or "@odata.nextLink" in data
            or "@odata.deltaLink" in data
            or type(data.get("value")) is not list
            or len(data["value"]) > MAX_USERS
            or type(data.get("@odata.count")) is not int
            or data["@odata.count"] != len(data["value"])):
        raise ProbeError("Incomplete or oversized Graph direct-user Group page")
    members = set()
    for entry in data["value"]:
        if (type(entry) is not dict
                or ("@odata.type" in entry
                    and entry["@odata.type"] != "#microsoft.graph.user")):
            raise ProbeError("Invalid Graph Group user projection")
        eid = _guid(entry.get("id"))
        if eid in members:
            raise ProbeError("Duplicate Graph Group user")
        members.add(eid)
    return members


def reconcile_full_groups(graph_origin, owg_base_url, confirm_owg_host,
                          confirm_provider, confirm_tenant, graph_token,
                          scim_token, raw_manifest, *, allow_loopback_http=False,
                          graph_opener=None, owg_opener=None):
    """Read-only bounded selected direct-user Group parity, with final rechecks."""
    tenant = _guid(confirm_tenant)
    origin = _graph_origin(graph_origin, allow_loopback_http)
    base = _base(owg_base_url, confirm_owg_host, allow_loopback_http)
    if (type(confirm_provider) is not str
            or _ID.fullmatch(confirm_provider) is None
            or base.rsplit("/", 1)[-1] != confirm_provider):
        raise ProbeError("OWG provider confirmation mismatch")
    users, groups = _manifest(raw_manifest, confirm_provider, tenant)
    graph_bearer = _graph_token(graph_token, tenant)
    owg_bearer = _token(scim_token)
    graph = graph_opener if graph_opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())
    owg = owg_opener if owg_opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())

    _scim_authority(owg, base, owg_bearer)
    selected_group_ids = {oid for oid, _ in groups.values()}
    for eid, (oid, expected) in groups.items():
        graph_members = _graph_group_users(graph, origin, graph_bearer, eid)
        if graph_members != expected:
            raise ProbeError("Entra full direct-user Group membership mismatch")
        owg_members = _owg_group(owg, base, owg_bearer, oid)
        if owg_members != {users[uid][0] for uid in expected}:
            raise ProbeError("OWG full direct-user Group membership mismatch")

    for eid, (oid, active) in users.items():
        if _graph_user(graph, origin, graph_bearer, eid) is not active:
            raise ProbeError("Entra selected lifecycle mismatch")
        projected = _owg_user(owg, base, owg_bearer, oid, active)
        expected = {oid for oid, members in groups.values() if eid in members}
        if projected.intersection(selected_group_ids) != expected:
            raise ProbeError("OWG reciprocal Group membership mismatch")

    first_eid, (first_oid, first_members) = next(iter(groups.items()))
    if _graph_group_users(graph, origin, graph_bearer, first_eid) != first_members:
        raise ProbeError("Entra Group changed during readback")
    if (_owg_group(owg, base, owg_bearer, first_oid)
            != {users[uid][0] for uid in first_members}):
        raise ProbeError("OWG Group changed during readback")
    _scim_authority(owg, base, owg_bearer)
    return {
        "format": FORMAT,
        "status": "selected_groups_direct_user_membership_match",
        "groups_checked": len(groups),
        "users_checked": len(users),
        "http_gets": 2 * len(groups) + 2 * len(users) + 4,
        "direct_user_group_parity": "selected_groups_only",
        "non_user_and_nested_membership": "not_verified",
        "snapshot_consistency": "not_atomic_graph_eventual",
        "provider_writes_performed": False,
        "customer_idp_provisioning_compatibility": "unverified",
    }


def main(argv=None, *, graph_opener=None, owg_opener=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-origin", default="https://graph.microsoft.com")
    parser.add_argument("--owg-base-url", required=True)
    parser.add_argument("--confirm-owg-host", required=True)
    parser.add_argument("--confirm-provider", required=True)
    parser.add_argument("--confirm-tenant", required=True)
    parser.add_argument("--consent-entra-group-read", action="store_true")
    parser.add_argument("--consent-owg-group-read", action="store_true")
    parser.add_argument("--consent-cross-system-reconcile", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not all((args.consent_entra_group_read, args.consent_owg_group_read,
                    args.consent_cross_system_reconcile)):
            raise ProbeError("Three explicit consents required")
        stream = stdin if stdin is not None else sys.stdin.buffer
        receipt = reconcile_full_groups(
            args.graph_origin, args.owg_base_url, args.confirm_owg_host,
            args.confirm_provider, args.confirm_tenant,
            _connector_token("OWG_ENTRA_READ_TOKEN"),
            os.environ.get("OWG_SCIM_PROBE_TOKEN"),
            stream.read(MAX_MANIFEST_BYTES + 1),
            allow_loopback_http=args.allow_loopback_http,
            graph_opener=graph_opener, owg_opener=owg_opener)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (ProbeError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused", "provider_writes_performed": False,
            "reason": "Check consents, complete direct-user Group mappings and current credentials",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
