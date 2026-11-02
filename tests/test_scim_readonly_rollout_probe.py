"""Read-only rollout probe: real OWG SCIM routes and real loopback transport."""
from __future__ import annotations
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from threading import Thread
from urllib import request
import pytest

from scripts.scim_readonly_rollout_probe import (
    GROUP, LIST, SPC, USER, ProbeError, main, probe,
)
from test_scim_routes import http, user

TOKEN = "owned-disposable-provider-token"

class _Response:
    def __init__(self, status, headers, body, url):
        self.status = status
        self.headers = headers
        self.body = io.BytesIO(body)
        self.url = url
    def geturl(self):
        return self.url
    def read(self, size):
        return self.body.read(size)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.body.close()

class _ActualASGIRoutes:
    """Use actual OWG FastAPI/SCIM production handlers, not fake responses."""
    def __init__(self, client):
        self.client = client
        self.requests = []
    def open(self, req, timeout):
        assert timeout == 10
        assert req.get_method() == "GET"
        self.requests.append(req.full_url)
        response = self.client.get(req.full_url, headers=dict(req.header_items()),
                                   follow_redirects=False)
        return _Response(response.status_code, response.headers,
                         response.content, req.full_url)

def test_actual_owg_scim_routes_with_existing_user_no_identity_download(http):
    client, db, owner, headers, base, pid = http
    made = client.post(base + "/Users", headers=headers, json=user())
    assert made.status_code == 201
    opener = _ActualASGIRoutes(client)
    result = probe("https://owg.example.test" + base, "owg.example.test",
                   headers["Authorization"][7:], opener=opener)
    assert result["status"] == "read_only_checks_passed"
    assert result["identity_resources_requested"] == 0
    assert result["provider_writes_performed"] is False
    assert len(opener.requests) == 5
    assert all("/Users/" not in url and "/Groups/" not in url
               for url in opener.requests)
    assert all(("count=0" in url) == ("/Users?" in url or "/Groups?" in url)
               for url in opener.requests)
    assert client.get(base + "/Users", headers=headers).json()["totalResults"] == 1
    assert made.json()["userName"] not in json.dumps(result)
    assert headers["Authorization"][7:] not in json.dumps(result)

def test_actual_scim_disabled_and_revoked_provider_fail_closed(http):
    client, db, owner, headers, base, pid = http
    opener = _ActualASGIRoutes(client)
    with pytest.raises(ProbeError):
        probe("https://owg.example.test" + base, "owg.example.test",
              "wrong-provider-token-123456789", opener=opener)
    revoked = client.post("/v1/admin/scim-providers/acme/" + pid + "/revoke",
                          headers=owner, json={"revision": 2})
    assert revoked.status_code == 200
    with pytest.raises(ProbeError):
        probe("https://owg.example.test" + base, "owg.example.test",
              headers["Authorization"][7:], opener=opener)

@pytest.mark.parametrize("url,host,loopback", [
    ("http://example.test/scim/v2/abc", "example.test", False),
    ("http://example.test/scim/v2/abc", "example.test", True),
    ("https://example.test/scim/v2/abc", "wrong.test", False),
    ("https://user:pass@example.test/scim/v2/abc", "example.test", False),
    ("https://example.test/scim/v2/abc?x=1", "example.test", False),
    ("https://example.test/scim/v2/abc/Users", "example.test", False),
    ("https://example.test/scim/v2/abc#fragment", "example.test", False),
    ("https://example.test:0/scim/v2/abc", "example.test", False),
])
def test_bad_endpoint_never_opens_transport(url, host, loopback):
    class Forbidden:
        def open(self, *args, **kwargs):
            pytest.fail("Invalid endpoint contacted")
    with pytest.raises(ProbeError):
        probe(url, host, TOKEN, allow_loopback_http=loopback, opener=Forbidden())

def test_missing_consent_and_no_token_do_not_contact_actual_server(http, monkeypatch, capsys):
    client, db, owner, headers, base, pid = http
    opener = _ActualASGIRoutes(client)
    args = ["--base-url", "https://owg.example.test" + base,
            "--confirm-host", "owg.example.test"]
    monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN", headers["Authorization"][7:])
    assert main(args, opener=opener) == 2
    assert opener.requests == []
    monkeypatch.delenv("OWG_SCIM_PROBE_TOKEN")
    assert main(args + ["--consent-readonly-probe"], opener=opener) == 2
    assert opener.requests == []
    output = capsys.readouterr().out
    assert "token" not in output.lower() or "current token" in output.lower()
    assert headers["Authorization"][7:] not in output

def _list(resources):
    return {"schemas": [LIST], "totalResults": len(resources),
            "startIndex": 1, "itemsPerPage": len(resources), "Resources": resources}

def _fixtures():
    return {
        "/ServiceProviderConfig": {"schemas": [SPC], "patch": {"supported": True},
            "etag": {"supported": True}, "filter": {"supported": True},
            "bulk": {"supported": True},
            "authenticationSchemes": [{"type": "oauthbearertoken"}]},
        "/ResourceTypes": _list([
            {"id": "User", "endpoint": "/Users", "schema": USER},
            {"id": "Group", "endpoint": "/Groups", "schema": GROUP}]),
        "/Schemas": _list([{"id": USER}, {"id": GROUP}]),
        "/Users?startIndex=1&count=0": _list([]),
        "/Groups?startIndex=1&count=0": _list([]),
    }

@contextmanager
def _local_server(*, bad_path=None, bad_value=None, redirect=False):
    fixtures = _fixtures()
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            if redirect:
                self.send_response(302)
                self.send_header("Location", "http://example.test/steal")
                self.end_headers()
                return
            suffix = self.path.removeprefix("/scim/v2/owned")
            value = bad_value if suffix == bad_path else fixtures.get(suffix)
            if value is None:
                self.send_response(404)
                self.end_headers()
                return
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/scim+json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            pytest.fail("Read-only probe attempted a write")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port) + "/scim/v2/owned", calls
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()

def test_real_tcp_loopback_five_gets_and_no_proxy_or_identity_download():
    with _local_server() as (base, calls):
        result = probe(base, "127.0.0.1", TOKEN, allow_loopback_http=True)
    assert result["status"] == "read_only_checks_passed"
    assert len(calls) == 5
    assert all(method == "GET" and credential == "Bearer " + TOKEN
               for method, _, credential in calls)
    assert all(path.endswith("count=0") for _, path, _ in calls[-2:])

@pytest.mark.parametrize("path,value", [
    ("/Users?startIndex=1&count=0", {"schemas": [LIST], "totalResults": 1,
        "startIndex": 1, "itemsPerPage": 1, "Resources": [{"userName": "secret"}]}),
    ("/Schemas", b'{"schemas":[],"schemas":[]}'),
    ("/ServiceProviderConfig", b"{" + b"x" * 65537 + b"}"),
    ("/ResourceTypes", _list([{"id": "User", "endpoint": "/Users", "schema": USER}])),
])
def test_real_tcp_invalid_response_refused(path, value):
    with _local_server(bad_path=path, bad_value=value) as (base, calls):
        with pytest.raises(ProbeError):
            probe(base, "127.0.0.1", TOKEN, allow_loopback_http=True)
    assert all(method == "GET" for method, _, _ in calls)

def test_real_tcp_redirect_does_not_forward_bearer():
    with _local_server(redirect=True) as (base, calls):
        with pytest.raises(ProbeError):
            probe(base, "127.0.0.1", TOKEN, allow_loopback_http=True)
    assert len(calls) == 1

def test_cli_success_against_actual_owg_scim_routes(http, monkeypatch, capsys):
    client, db, owner, headers, base, pid = http
    opener = _ActualASGIRoutes(client)
    token = headers["Authorization"][7:]
    monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN", token)
    code = main(["--base-url", "https://owg.example.test" + base,
                 "--confirm-host", "owg.example.test",
                 "--consent-readonly-probe"], opener=opener)
    assert code == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "read_only_checks_passed"
    assert receipt["identity_resources_requested"] == 0
    assert receipt["provider_writes_performed"] is False
    assert len(opener.requests) == 5
    assert token not in json.dumps(receipt)
    assert pid not in json.dumps(receipt)

