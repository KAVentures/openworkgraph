"""Live SCIM cross-provider isolation, using actual OWG routes and TCP transport."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from scripts.scim_provider_isolation_probe import isolation_probe, main, ProbeError
from test_scim_readonly_rollout_probe import _ActualASGIRoutes, _Response


def _second(http):
    client, db, owner, headers, base, pid = http
    created = client.post(
        "/v1/admin/scim-providers/acme", headers=owner,
        json={"label": "Separate owned test IdP"},
    )
    assert created.status_code == 201, created.text
    other_id = created.json()["provider"]["provider_id"]
    token = created.json()["token"]
    enabled = client.patch(
        "/v1/admin/scim-providers/acme/" + other_id, headers=owner,
        json={"revision": 1, "enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    return "/scim/v2/" + other_id, other_id, token


def _run(http, opener, *, token_b=None):
    client, db, owner, headers, base, pid = http
    base_b, pid_b, second = _second(http)
    receipt = isolation_probe(
        "https://owg.example.test" + base,
        "https://owg.example.test" + base_b,
        "owg.example.test", pid, pid_b,
        headers["Authorization"][7:], second if token_b is None else token_b,
        opener=opener,
    )
    return receipt, base_b, pid_b, second


def test_two_actual_owg_providers_cross_tokens_denied_without_identity_reads(http):
    client, db, owner, headers, base, pid = http
    opener = _ActualASGIRoutes(client)
    result, base_b, pid_b, second = _run(http, opener)
    assert result["status"] == "cross_provider_read_isolation_passed"
    assert result["http_gets"] == 14
    assert result["identity_resources_requested"] == 0
    assert result["provider_writes_performed"] is False
    assert len(opener.requests) == 14
    assert all("/Users/" not in u and "/Groups/" not in u for u in opener.requests)
    assert all("/ServiceProviderConfig" in u or "/ResourceTypes" in u
               or "/Schemas" in u or "count=0" in u for u in opener.requests)
    output = json.dumps(result)
    assert all(secret not in output for secret in
               (pid, pid_b, headers["Authorization"][7:], second))


@pytest.mark.parametrize("bad", ["same-provider", "same-token", "different-origin",
                                 "wrong-confirmation"])
def test_invalid_pair_refuses_before_transport(http, bad):
    client, db, owner, headers, base, pid = http
    base_b, pid_b, token_b = _second(http)
    a = "https://owg.example.test" + base
    b = "https://owg.example.test" + base_b
    confirm_b = pid_b
    token_a = headers["Authorization"][7:]
    if bad == "same-provider":
        b, confirm_b = a, pid
    elif bad == "same-token":
        token_b = token_a
    elif bad == "different-origin":
        b = "https://other.example.test" + base_b
    else:
        confirm_b = "wrong-provider"
    class Forbidden:
        def open(self, *args, **kwargs):
            pytest.fail("Preflight contacted a provider")
    with pytest.raises(ProbeError):
        isolation_probe(a, b, "owg.example.test", pid, confirm_b,
                        token_a, token_b, opener=Forbidden())


@pytest.mark.parametrize("unexpected", [200, 403, 404])
def test_cross_provider_non_401_never_passes(http, unexpected):
    client, db, owner, headers, base, pid = http
    base_b, pid_b, token_b = _second(http)
    class CrossResponse(_ActualASGIRoutes):
        def open(self, req, timeout):
            if len(self.requests) == 10:
                self.requests.append(req.full_url)
                return _Response(unexpected, {
                    "Content-Type": "application/scim+json",
                    "Cache-Control": "no-store",
                }, b"{}", req.full_url)
            return super().open(req, timeout)
    opener = CrossResponse(client)
    with pytest.raises(ProbeError):
        isolation_probe(
            "https://owg.example.test" + base,
            "https://owg.example.test" + base_b,
            "owg.example.test", pid, pid_b,
            headers["Authorization"][7:], token_b, opener=opener,
        )
    assert len(opener.requests) == 11


def test_revoked_token_during_negative_phase_cannot_false_pass(http):
    client, db, owner, headers, base, pid = http
    base_b, pid_b, token_b = _second(http)
    class RevokeAfterCross(_ActualASGIRoutes):
        def open(self, req, timeout):
            response = super().open(req, timeout)
            if len(self.requests) == 12:
                revoked = client.post(
                    "/v1/admin/scim-providers/acme/" + pid_b + "/revoke",
                    headers=owner, json={"revision": 2},
                )
                assert revoked.status_code == 200
            return response
    opener = RevokeAfterCross(client)
    with pytest.raises(ProbeError):
        isolation_probe(
            "https://owg.example.test" + base,
            "https://owg.example.test" + base_b,
            "owg.example.test", pid, pid_b,
            headers["Authorization"][7:], token_b, opener=opener,
        )
    assert len(opener.requests) == 14


def test_cli_requires_explicit_consent_and_distinct_tokens(http, monkeypatch, capsys):
    client, db, owner, headers, base, pid = http
    base_b, pid_b, token_b = _second(http)
    opener = _ActualASGIRoutes(client)
    args = [
        "--base-url-a", "https://owg.example.test" + base,
        "--base-url-b", "https://owg.example.test" + base_b,
        "--confirm-host", "owg.example.test",
        "--confirm-provider-a", pid, "--confirm-provider-b", pid_b,
    ]
    monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN_A", headers["Authorization"][7:])
    monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN_B", token_b)
    assert main(args, opener=opener) == 2
    assert opener.requests == []
    assert main(args + ["--consent-cross-provider-read"], opener=opener) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["status"] == "cross_provider_read_isolation_passed"
    assert len(opener.requests) == 14
    assert pid not in json.dumps(result) and pid_b not in json.dumps(result)
    assert token_b not in json.dumps(result)


@contextmanager
def _tcp_scim(client):
    """Actual TCP GETs bridged into the production OWG FastAPI SCIM router."""
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            response = client.get(
                self.path, headers={"Authorization": self.headers.get("Authorization", ""),
                                    "Accept": self.headers.get("Accept", ""),
                                    "Cache-Control": self.headers.get("Cache-Control", "")},
                follow_redirects=False,
            )
            self.send_response(response.status_code)
            for name in ("content-type", "cache-control"):
                if name in response.headers:
                    self.send_header(name, response.headers[name])
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        def do_POST(self):
            pytest.fail("Isolation probe must not write")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), calls
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_actual_owg_scim_router_over_real_tcp_loopback(http):
    client, db, owner, headers, base, pid = http
    base_b, pid_b, token_b = _second(http)
    with _tcp_scim(client) as (origin, calls):
        result = isolation_probe(
            origin + base, origin + base_b, "127.0.0.1",
            pid, pid_b, headers["Authorization"][7:], token_b,
            allow_loopback_http=True,
        )
    assert result["status"] == "cross_provider_read_isolation_passed"
    assert len(calls) == 14
    assert all("/Users/" not in path and "/Groups/" not in path for path in calls)
