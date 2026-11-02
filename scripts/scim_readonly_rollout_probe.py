"""Opt-in, read-only SCIM rollout probe against an owned OWG provider endpoint.

Five bounded GETs. No identity records requested, token persistence, writes,
automatic retries or customer IdP compatibility certification.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from urllib import error, parse, request

FORMAT = "openworkgraph.scim-readonly-rollout-probe.v1"
MAX_BYTES = 65536
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SPC = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
USER = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
_PATH = re.compile(r"/scim/v2/[A-Za-z0-9_-]{1,100}\Z", re.ASCII)

class ProbeError(ValueError):
    """Minimized error without credentials or identities."""

class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError("Redirect refused")

def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError("Duplicate JSON key")
        result[key] = value
    return result

def _nonfinite(_):
    raise ProbeError("Nonfinite JSON")

def _base(value, confirm_host, allow_loopback_http):
    if type(value) is not str or len(value) > 500 or type(confirm_host) is not str:
        raise ProbeError("Invalid endpoint")
    try:
        parsed = parse.urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ProbeError("Invalid endpoint") from None
    if (not host or not host.isascii() or not confirm_host.isascii()
            or host.lower() != confirm_host.lower()
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or not _PATH.fullmatch(parsed.path)
            or (port is not None and not 1 <= port <= 65535)
            or any(ord(c) < 33 or ord(c) == 127 for c in value)):
        raise ProbeError("Endpoint/host mismatch")
    if parsed.scheme != "https" and not (
            parsed.scheme == "http" and allow_loopback_http
            and host.lower() in {"localhost", "127.0.0.1", "::1"}):
        raise ProbeError("HTTPS required")
    return value

def _token(value):
    if (type(value) is not str or not 16 <= len(value) <= 200
            or not value.isascii()
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)):
        raise ProbeError("Current provider token required")
    return value

def _get(opener, url, bearer):
    req = request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + bearer,
        "Accept": "application/scim+json", "Cache-Control": "no-store",
    })
    try:
        with opener.open(req, timeout=10) as response:
            status = response.status
            final = response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            cache = response.headers.get("Cache-Control", "").lower()
            raw = response.read(MAX_BYTES + 1)
    except (OSError, error.URLError, ValueError, ProbeError):
        raise ProbeError("SCIM read refused") from None
    if (status != 200 or final != url or media != "application/scim+json"
            or "no-store" not in {x.strip() for x in cache.split(",")}
            or len(raw) > MAX_BYTES):
        raise ProbeError("SCIM response contract refused")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_nonfinite)
    except (UnicodeError, ValueError, RecursionError, TypeError):
        raise ProbeError("Invalid SCIM JSON") from None
    if type(value) is not dict:
        raise ProbeError("Invalid SCIM response")
    return value

def _list(value, *, empty=False):
    if (value.get("schemas") != [LIST]
            or type(value.get("totalResults")) is not int or value["totalResults"] < 0
            or type(value.get("startIndex")) is not int or value["startIndex"] != 1
            or type(value.get("itemsPerPage")) is not int
            or type(value.get("Resources")) is not list
            or value["itemsPerPage"] != len(value["Resources"])
            or len(value["Resources"]) > 200
            or (empty and (value["Resources"] or value["itemsPerPage"] != 0))):
        raise ProbeError("SCIM collection envelope refused")
    return value["Resources"]

def probe(base_url, confirm_host, token, *, allow_loopback_http=False, opener=None):
    """Use real read-only SCIM GETs; do not retrieve or display roster resources."""
    base = _base(base_url, confirm_host, allow_loopback_http)
    bearer = _token(token)
    transport = opener if opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())
    config = _get(transport, base + "/ServiceProviderConfig", bearer)
    schemes = config.get("authenticationSchemes")
    if (config.get("schemas") != [SPC]
            or not all(type(config.get(k)) is dict and config[k].get("supported") is True
                       for k in ("patch", "etag", "filter", "bulk"))
            or type(schemes) is not list
            or not any(type(x) is dict and x.get("type") == "oauthbearertoken"
                       for x in schemes)):
        raise ProbeError("Required SCIM discovery subset missing")
    resource_types = _list(_get(transport, base + "/ResourceTypes", bearer))
    schemas = _list(_get(transport, base + "/Schemas", bearer))
    found = {}
    for item in resource_types:
        if type(item) is not dict or type(item.get("id")) is not str:
            raise ProbeError("Invalid SCIM resource type")
        if item["id"] in found:
            raise ProbeError("Duplicate SCIM resource type")
        found[item["id"]] = item
    if (set(found) != {"User", "Group"}
            or found["User"].get("endpoint") != "/Users"
            or found["Group"].get("endpoint") != "/Groups"
            or found["User"].get("schema") != USER
            or found["Group"].get("schema") != GROUP
            or len(schemas) != 2
            or {x.get("id") for x in schemas if type(x) is dict} != {USER, GROUP}):
        raise ProbeError("SCIM resource/schema contract mismatch")
    for collection in ("Users", "Groups"):
        _list(_get(transport, base + "/" + collection + "?startIndex=1&count=0", bearer),
              empty=True)
    return {"format": FORMAT, "status": "read_only_checks_passed",
            "checked": ["ServiceProviderConfig", "ResourceTypes", "Schemas",
                        "Users count=0", "Groups count=0"],
            "identity_resources_requested": 0, "provider_writes_performed": False,
            "customer_idp_compatibility": "unverified"}

def main(argv=None, *, opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--confirm-host", required=True)
    parser.add_argument("--consent-readonly-probe", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.consent_readonly_probe:
            raise ProbeError("Explicit read consent required")
        result = probe(args.base_url, args.confirm_host,
                       os.environ.get("OWG_SCIM_PROBE_TOKEN"),
                       allow_loopback_http=args.allow_loopback_http, opener=opener)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ProbeError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "identity_resources_requested": 0,
                          "provider_writes_performed": False,
                          "reason": "Check current token, confirmed endpoint and SCIM read contract"},
                         sort_keys=True))
        return 2

if __name__ == "__main__":
    sys.exit(main())
