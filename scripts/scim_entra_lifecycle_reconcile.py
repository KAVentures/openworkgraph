"""Explicitly consented live Microsoft Entra -> OWG SCIM lifecycle reconciliation.

Read-only Microsoft Graph + OWG SCIM GETs for 1–8 operator-selected mappings.
No directory enumeration, identity/credential output, retries or provisioning writes.
"""
from __future__ import annotations

from server.connector_oauth import connector_token as _connector_token
import argparse
import base64
import json
import os
import re
import sys
from urllib import error, parse, request

from .scim_lifecycle_readback_probe import _ID
from .scim_readonly_rollout_probe import (
    SPC, USER, ProbeError, _NoRedirect, _base, _get, _nonfinite, _token, _unique,
)

FORMAT = "openworkgraph.scim-entra-lifecycle-reconcile.v1"
MAX_MANIFEST_BYTES = 16384
MAX_GRAPH_BYTES = 65536
_GUID = re.compile(
    r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z", re.ASCII
)


def _guid(value):
    if type(value) is not str or _GUID.fullmatch(value) is None:
        raise ProbeError("Invalid selected directory identifier")
    return value.lower()


def _manifest(raw, provider, tenant):
    if type(raw) is not bytes or len(raw) > MAX_MANIFEST_BYTES:
        raise ProbeError("Bounded private selection required")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProbeError("Invalid private selection") from None
    if (type(value) is not dict
            or set(value) != {"format", "provider_id", "entra_tenant_id", "users"}
            or value["format"] != FORMAT or value["provider_id"] != provider
            or _guid(value["entra_tenant_id"]) != tenant
            or type(value["users"]) is not list
            or not 1 <= len(value["users"]) <= 8):
        raise ProbeError("Selection scope mismatch")
    selected = []
    entra_seen, owg_seen = set(), set()
    for item in value["users"]:
        if (type(item) is not dict
                or set(item) != {"entra_id", "owg_id", "expected_active"}
                or type(item["owg_id"]) is not str
                or _ID.fullmatch(item["owg_id"]) is None
                or type(item["expected_active"]) is not bool):
            raise ProbeError("Invalid selected mapping")
        eid = _guid(item["entra_id"])
        oid = item["owg_id"]
        if eid in entra_seen or oid in owg_seen:
            raise ProbeError("Duplicate selected mapping")
        entra_seen.add(eid)
        owg_seen.add(oid)
        selected.append((eid, oid, item["expected_active"]))
    return selected


def _graph_origin(value, allow_loopback_http):
    if type(value) is not str or len(value) > 200:
        raise ProbeError("Invalid Microsoft Graph origin")
    try:
        parts = parse.urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ProbeError("Invalid Microsoft Graph origin") from None
    if (not host or parts.username is not None or parts.password is not None
            or parts.path or parts.query or parts.fragment
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ProbeError("Unconfirmed Microsoft Graph origin")
    if parts.scheme == "https" and host == "graph.microsoft.com" and port is None:
        return value
    if (allow_loopback_http and parts.scheme == "http"
            and host in {"127.0.0.1", "localhost"} and port is not None
            and 1 <= port <= 65535):
        return value
    raise ProbeError("Microsoft Graph production origin must be pinned")


def _graph_token(value, tenant):
    """The JWT tenant hint is *not* authentication; Graph validates the token."""
    if (type(value) is not str or not 64 <= len(value) <= 8192
            or not value.isascii()
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)):
        raise ProbeError("Current Microsoft Graph bearer required")
    parts = value.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise ProbeError("Microsoft Graph tenant hint unavailable")
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        if len(payload) > 8192:
            raise ProbeError("Oversized tenant hint")
        claims = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique,
                            parse_constant=_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProbeError("Invalid Microsoft Graph tenant hint") from None
    if type(claims) is not dict or _guid(claims.get("tid")) != tenant:
        raise ProbeError("Microsoft Graph tenant confirmation mismatch")
    return value


def _graph_user(opener, origin, bearer, user_id):
    url = origin + "/v1.0/users/" + user_id + "?%24select=id%2CaccountEnabled"
    req = request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + bearer,
        "Accept": "application/json",
        "Cache-Control": "no-store",
    })
    try:
        with opener.open(req, timeout=10) as response:
            status = response.status
            final = response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
            raw = response.read(MAX_GRAPH_BYTES + 1)
    except (OSError, error.URLError, ValueError, ProbeError):
        raise ProbeError("Microsoft Graph selected read refused") from None
    if (status != 200 or final != url or media != "application/json"
            or len(raw) > MAX_GRAPH_BYTES):
        raise ProbeError("Microsoft Graph selected response refused")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProbeError("Invalid Microsoft Graph selected response") from None
    if (type(value) is not dict or _guid(value.get("id")) != user_id
            or type(value.get("accountEnabled")) is not bool):
        raise ProbeError("Microsoft Graph selected state mismatch")
    return value["accountEnabled"]


def _scim_authority(opener, base, token):
    config = _get(opener, base + "/ServiceProviderConfig", token)
    if (config.get("schemas") != [SPC] or type(config.get("patch")) is not dict
            or config["patch"].get("supported") is not True):
        raise ProbeError("Current OWG provider authorization unverified")


def _scim_user(opener, base, token, user_id):
    value = _get(opener, base + "/Users/" + user_id, token)
    meta = value.get("meta")
    if (value.get("schemas") != [USER] or value.get("id") != user_id
            or type(value.get("active")) is not bool
            or type(meta) is not dict or meta.get("resourceType") != "User"
            or type(meta.get("version")) is not str
            or not 1 <= len(meta["version"]) <= 200):
        raise ProbeError("OWG selected User identity/version refused")
    return value["active"]


def reconcile(graph_origin, owg_base_url, confirm_owg_host, confirm_provider,
              confirm_tenant, graph_token, scim_token, raw_manifest,
              *, allow_loopback_http=False, graph_opener=None, owg_opener=None):
    """2*N+3 real read-only GETs; all three-way lifecycle states must agree."""
    tenant = _guid(confirm_tenant)
    origin = _graph_origin(graph_origin, allow_loopback_http)
    base = _base(owg_base_url, confirm_owg_host, allow_loopback_http)
    if (type(confirm_provider) is not str
            or _ID.fullmatch(confirm_provider) is None
            or base.rsplit("/", 1)[-1] != confirm_provider):
        raise ProbeError("OWG provider confirmation mismatch")
    selected = _manifest(raw_manifest, confirm_provider, tenant)
    graph_bearer = _graph_token(graph_token, tenant)
    owg_bearer = _token(scim_token)
    graph_transport = graph_opener if graph_opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())
    owg_transport = owg_opener if owg_opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())

    _scim_authority(owg_transport, base, owg_bearer)
    for entra_id, owg_id, expected in selected:
        observed_entra = _graph_user(graph_transport, origin, graph_bearer, entra_id)
        observed_owg = _scim_user(owg_transport, base, owg_bearer, owg_id)
        if observed_entra is not expected or observed_owg is not expected:
            raise ProbeError("Cross-system lifecycle mismatch")
    # Recheck both independently after the selected reads. This is not an
    # atomic snapshot and cannot prove offboarding of other credentials.
    first_entra, _, first_expected = selected[0]
    if _graph_user(graph_transport, origin, graph_bearer, first_entra) is not first_expected:
        raise ProbeError("Microsoft Graph state changed during reconciliation")
    _scim_authority(owg_transport, base, owg_bearer)
    return {
        "format": FORMAT, "status": "selected_lifecycle_states_match",
        "users_checked": len(selected), "http_gets": len(selected) * 2 + 3,
        "provider_writes_performed": False, "directory_enumeration": False,
        "snapshot_consistency": "not_atomic",
        "device_sso_invitation_revocation": "not_verified",
        "customer_idp_provisioning_compatibility": "unverified",
    }


def main(argv=None, *, graph_opener=None, owg_opener=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-origin", default="https://graph.microsoft.com")
    parser.add_argument("--owg-base-url", required=True)
    parser.add_argument("--confirm-owg-host", required=True)
    parser.add_argument("--confirm-provider", required=True)
    parser.add_argument("--confirm-tenant", required=True)
    parser.add_argument("--consent-entra-read", action="store_true")
    parser.add_argument("--consent-owg-read", action="store_true")
    parser.add_argument("--consent-cross-system-reconcile", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not all((args.consent_entra_read, args.consent_owg_read,
                    args.consent_cross_system_reconcile)):
            raise ProbeError("Three explicit consents required")
        stream = stdin if stdin is not None else sys.stdin.buffer
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
        receipt = reconcile(
            args.graph_origin, args.owg_base_url, args.confirm_owg_host,
            args.confirm_provider, args.confirm_tenant,
            _connector_token("OWG_ENTRA_READ_TOKEN"),
            os.environ.get("OWG_SCIM_PROBE_TOKEN"), raw,
            allow_loopback_http=args.allow_loopback_http,
            graph_opener=graph_opener, owg_opener=owg_opener)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (ProbeError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused", "provider_writes_performed": False,
            "reason": "Check explicit consent, confirmed tenants/providers, current read credentials and selected states",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
