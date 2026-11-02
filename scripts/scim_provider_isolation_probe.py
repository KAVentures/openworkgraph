"""Operator-authorized, read-only cross-provider SCIM credential isolation probe.

Uses the real running OWG SCIM HTTP endpoints. Never creates or modifies an
identity, provider, employee, permission, or stored credential.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error, parse, request

from .scim_readonly_rollout_probe import (
    FORMAT as READINESS_FORMAT, SPC, ProbeError, _NoRedirect, _base, _get,
    _token, probe,
)

FORMAT = "openworkgraph.scim-provider-isolation-probe.v1"


def _transport(opener):
    return opener if opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect(),
    )


def _deny_cross(opener, url: str, token: str) -> None:
    """A cross-provider GET must fail with *exactly* unauthorized, not 404/200.

    A 401 alone cannot establish isolation when a token is revoked. The
    caller checks both credentials against their own endpoints before AND
    after the two negative probes.
    """
    req = request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/scim+json",
        "Cache-Control": "no-store",
    })
    try:
        with opener.open(req, timeout=10) as response:
            status = response.status
            final = response.geturl()
            # Never read a cross-provider body: it could contain identities.
    except error.HTTPError as exc:
        status = exc.code
        final = exc.geturl()
        exc.close()
    except (OSError, error.URLError, ValueError, ProbeError):
        raise ProbeError("Cross-provider read refused") from None
    if status != 401 or final != url:
        raise ProbeError("Cross-provider authorization boundary unverified")


def isolation_probe(base_a, base_b, confirm_host, confirm_provider_a,
                    confirm_provider_b, token_a, token_b, *,
                    allow_loopback_http=False, opener=None):
    """14 bounded GETs: 5+5 own readiness, 2 cross-denials, 2 own rechecks."""
    a = _base(base_a, confirm_host, allow_loopback_http)
    b = _base(base_b, confirm_host, allow_loopback_http)
    parsed_a, parsed_b = parse.urlsplit(a), parse.urlsplit(b)
    if (parsed_a.scheme != parsed_b.scheme
            or parsed_a.netloc.lower() != parsed_b.netloc.lower()
            or parsed_a.path == parsed_b.path
            or parsed_a.path.rsplit("/", 1)[-1] != confirm_provider_a
            or parsed_b.path.rsplit("/", 1)[-1] != confirm_provider_b
            or type(confirm_provider_a) is not str
            or type(confirm_provider_b) is not str
            or confirm_provider_a == confirm_provider_b):
        raise ProbeError("Two distinct confirmed providers on one origin required")
    first, second = _token(token_a), _token(token_b)
    if first == second:
        raise ProbeError("Distinct provider credentials required")
    transport = _transport(opener)

    # These validate both currently authorized discovery and count=0 collections,
    # without retrieving a single User or Group resource.
    probe(a, confirm_host, first, allow_loopback_http=allow_loopback_http,
          opener=transport)
    probe(b, confirm_host, second, allow_loopback_http=allow_loopback_http,
          opener=transport)
    _deny_cross(transport, b + "/ServiceProviderConfig", first)
    _deny_cross(transport, a + "/ServiceProviderConfig", second)

    # Avoid treating a revoked token's 401 on the other provider as proof.
    for base, token in ((a, first), (b, second)):
        current = _get(transport, base + "/ServiceProviderConfig", token)
        if (current.get("schemas") != [SPC]
                or type(current.get("patch")) is not dict
                or current["patch"].get("supported") is not True):
            raise ProbeError("Provider authorization no longer verified")
    return {
        "format": FORMAT,
        "status": "cross_provider_read_isolation_passed",
        "own_provider_readiness_checks": 2,
        "cross_provider_denials": 2,
        "http_gets": 14,
        "identity_resources_requested": 0,
        "provider_writes_performed": False,
        "customer_idp_compatibility": "unverified",
        "readiness_format": READINESS_FORMAT,
    }


def main(argv=None, *, opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url-a", required=True)
    parser.add_argument("--base-url-b", required=True)
    parser.add_argument("--confirm-host", required=True)
    parser.add_argument("--confirm-provider-a", required=True)
    parser.add_argument("--confirm-provider-b", required=True)
    parser.add_argument("--consent-cross-provider-read", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.consent_cross_provider_read:
            raise ProbeError("Explicit cross-provider read consent required")
        receipt = isolation_probe(
            args.base_url_a, args.base_url_b, args.confirm_host,
            args.confirm_provider_a, args.confirm_provider_b,
            os.environ.get("OWG_SCIM_PROBE_TOKEN_A"),
            os.environ.get("OWG_SCIM_PROBE_TOKEN_B"),
            allow_loopback_http=args.allow_loopback_http, opener=opener,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (ProbeError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused",
            "identity_resources_requested": 0,
            "provider_writes_performed": False,
            "reason": "Check both current provider tokens, endpoints, consent and SCIM read policy",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
