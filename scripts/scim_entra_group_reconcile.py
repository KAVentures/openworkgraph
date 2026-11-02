"""Consent-gated live Entra direct-group -> OWG SCIM membership reconciliation.

Selected identities only. Real read-only Microsoft Graph and OWG SCIM GETs;
no provisioning, discovery of users, automatic retries or permission changes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error, request

from .scim_entra_lifecycle_reconcile import (
    _graph_origin, _graph_token, _graph_user, _guid, _scim_authority,
)
from .scim_group_membership_readback_probe import _versioned
from .scim_lifecycle_readback_probe import _ID
from .scim_readonly_rollout_probe import (
    ProbeError, _NoRedirect, _base, _get, _nonfinite, _token, _unique,
)

FORMAT = "openworkgraph.scim-entra-group-reconcile.v1"
MAX_MANIFEST_BYTES = 16384
MAX_GRAPH_BYTES = 65536
MAX_DIRECT_GROUPS = 100


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
            or not 1 <= len(data["users"]) <= 8
            or type(data["groups"]) is not list
            or not 1 <= len(data["groups"]) <= 4):
        raise ProbeError("Selection scope mismatch")
    users = {}
    owg_ids = set()
    for item in data["users"]:
        if (type(item) is not dict
                or set(item) != {"entra_id", "owg_id", "expected_active"}
                or type(item["owg_id"]) is not str
                or _ID.fullmatch(item["owg_id"]) is None
                or type(item["expected_active"]) is not bool):
            raise ProbeError("Invalid selected user mapping")
        eid, oid = _guid(item["entra_id"]), item["owg_id"]
        if eid in users or oid in owg_ids:
            raise ProbeError("Duplicate selected user mapping")
        users[eid] = (oid, item["expected_active"])
        owg_ids.add(oid)
    groups = {}
    owg_groups = set()
    for item in data["groups"]:
        if (type(item) is not dict
                or set(item) != {"entra_id", "owg_id", "member_entra_ids"}
                or type(item["owg_id"]) is not str
                or _ID.fullmatch(item["owg_id"]) is None
                or not item["owg_id"].startswith("grp_")
                or type(item["member_entra_ids"]) is not list
                or len(item["member_entra_ids"]) > len(users)):
            raise ProbeError("Invalid selected group mapping")
        gid, oid = _guid(item["entra_id"]), item["owg_id"]
        if gid in groups or oid in owg_groups:
            raise ProbeError("Duplicate selected group mapping")
        members = [_guid(v) for v in item["member_entra_ids"]]
        if (len(set(members)) != len(members)
                or any(uid not in users or not users[uid][1] for uid in members)):
            raise ProbeError("Contradictory selected group membership")
        groups[gid] = (oid, set(members))
        owg_groups.add(oid)
    return users, groups


def _graph_direct_groups(opener, origin, bearer, entra_user):
    # Graph documents the direct memberOf cast. No transitive membership,
    # delta query or directory-wide listing. Refuse incomplete pagination.
    url = (origin + "/v1.0/users/" + entra_user
           + "/memberOf/microsoft.graph.group?%24select=id&%24top=100&%24count=true")
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
    except (OSError, error.URLError, ValueError):
        raise ProbeError("Microsoft Graph direct-membership read refused") from None
    if (status != 200 or final != url or media != "application/json"
            or len(raw) > MAX_GRAPH_BYTES):
        raise ProbeError("Microsoft Graph direct-membership response refused")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProbeError("Invalid Microsoft Graph direct-membership response") from None
    if (type(data) is not dict or "@odata.nextLink" in data
            or type(data.get("value")) is not list
            or len(data["value"]) > MAX_DIRECT_GROUPS):
        raise ProbeError("Incomplete or invalid direct-membership page")
    observed = set()
    for entry in data["value"]:
        if type(entry) is not dict:
            raise ProbeError("Invalid direct group projection")
        gid = _guid(entry.get("id"))
        if gid in observed:
            raise ProbeError("Duplicate direct group projection")
        observed.add(gid)
    return observed


def _owg_user(opener, base, bearer, uid, active):
    resource = _get(opener, base + "/Users/" + uid, bearer)
    _versioned(resource, "User", uid)
    if type(resource.get("active")) is not bool or resource["active"] is not active:
        raise ProbeError("OWG selected lifecycle mismatch")
    projected = resource.get("groups", [])
    if type(projected) is not list or len(projected) > 100:
        raise ProbeError("OWG User group projection refused")
    ids = set()
    for entry in projected:
        if (type(entry) is not dict
                or set(entry) != {"value", "display", "type"}
                or type(entry.get("value")) is not str
                or _ID.fullmatch(entry["value"]) is None
                or type(entry.get("display")) is not str
                or entry.get("type") != "direct"
                or entry["value"] in ids):
            raise ProbeError("Invalid OWG User direct group projection")
        ids.add(entry["value"])
    return ids


def _owg_group(opener, base, bearer, gid):
    resource = _get(opener, base + "/Groups/" + gid, bearer)
    _versioned(resource, "Group", gid)
    members = resource.get("members")
    if type(members) is not list or len(members) > 200:
        raise ProbeError("OWG Group member projection refused")
    ids = set()
    for member in members:
        if (type(member) is not dict
                or set(member) != {"value", "type"}
                or member.get("type") != "User"
                or type(member.get("value")) is not str
                or _ID.fullmatch(member["value"]) is None
                or member["value"] in ids):
            raise ProbeError("Invalid OWG Group member projection")
        ids.add(member["value"])
    return ids


def reconcile_groups(graph_origin, owg_base_url, confirm_owg_host, confirm_provider,
                     confirm_tenant, graph_token, scim_token, raw_manifest,
                     *, allow_loopback_http=False, graph_opener=None, owg_opener=None):
    """3*N+G+5 bounded GETs; selected direct membership, not whole-group parity."""
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
    selected_group_ids = set(groups)
    selected_owg_group_ids = {oid for oid, _ in groups.values()}
    for eid, (oid, expected_active) in users.items():
        if _graph_user(graph, origin, graph_bearer, eid) is not expected_active:
            raise ProbeError("Entra selected lifecycle mismatch")
        graph_memberships = _graph_direct_groups(graph, origin, graph_bearer, eid)
        expected_graph = {gid for gid, (_, members) in groups.items() if eid in members}
        if graph_memberships.intersection(selected_group_ids) != expected_graph:
            raise ProbeError("Entra selected direct membership mismatch")
        owg_memberships = _owg_user(owg, base, owg_bearer, oid, expected_active)
        expected_owg = {gid for _, (gid, members) in groups.items() if eid in members}
        if owg_memberships.intersection(selected_owg_group_ids) != expected_owg:
            raise ProbeError("OWG selected reciprocal membership mismatch")
    for _, (gid, member_eids) in groups.items():
        observed = _owg_group(owg, base, owg_bearer, gid)
        expected = {users[eid][0] for eid in member_eids}
        if observed.intersection({oid for oid, _ in users.values()}) != expected:
            raise ProbeError("OWG selected Group membership mismatch")

    # Recheck one selected user's lifecycle and direct membership, one OWG
    # Group and current provider authority. No atomic snapshot is implied.
    first_eid, (first_oid, first_active) = next(iter(users.items()))
    if _graph_user(graph, origin, graph_bearer, first_eid) is not first_active:
        raise ProbeError("Entra lifecycle changed during readback")
    if (_graph_direct_groups(graph, origin, graph_bearer, first_eid)
            .intersection(selected_group_ids)
            != {gid for gid, (_, members) in groups.items() if first_eid in members}):
        raise ProbeError("Entra membership changed during readback")
    first_gid, first_members = next(iter(groups.values()))
    if (_owg_group(owg, base, owg_bearer, first_gid)
            .intersection({oid for oid, _ in users.values()})
            != {users[eid][0] for eid in first_members}):
        raise ProbeError("OWG membership changed during readback")
    _scim_authority(owg, base, owg_bearer)
    return {
        "format": FORMAT, "status": "selected_direct_memberships_match",
        "users_checked": len(users), "groups_checked": len(groups),
        "http_gets": 3 * len(users) + len(groups) + 5,
        "provider_writes_performed": False,
        "whole_group_parity": "not_verified",
        "nested_transitive_memberships": "not_verified",
        "snapshot_consistency": "not_atomic",
        "customer_idp_provisioning_compatibility": "unverified",
    }


def main(argv=None, *, graph_opener=None, owg_opener=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-origin", default="https://graph.microsoft.com")
    parser.add_argument("--owg-base-url", required=True)
    parser.add_argument("--confirm-owg-host", required=True)
    parser.add_argument("--confirm-provider", required=True)
    parser.add_argument("--confirm-tenant", required=True)
    parser.add_argument("--consent-entra-membership-read", action="store_true")
    parser.add_argument("--consent-owg-membership-read", action="store_true")
    parser.add_argument("--consent-cross-system-reconcile", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not all((args.consent_entra_membership_read,
                    args.consent_owg_membership_read,
                    args.consent_cross_system_reconcile)):
            raise ProbeError("Three explicit consents required")
        stream = stdin if stdin is not None else sys.stdin.buffer
        receipt = reconcile_groups(
            args.graph_origin, args.owg_base_url, args.confirm_owg_host,
            args.confirm_provider, args.confirm_tenant,
            os.environ.get("OWG_ENTRA_READ_TOKEN"),
            os.environ.get("OWG_SCIM_PROBE_TOKEN"),
            stream.read(MAX_MANIFEST_BYTES + 1),
            allow_loopback_http=args.allow_loopback_http,
            graph_opener=graph_opener, owg_opener=owg_opener)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (ProbeError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused",
            "provider_writes_performed": False,
            "reason": "Check consents, selected direct memberships, scopes and current credentials",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
