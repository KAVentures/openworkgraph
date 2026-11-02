"""Microsoft Graph live read + actual OWG SCIM route/TCP reconciliation acceptance."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from threading import Thread
from urllib import parse

import pytest

from scripts.scim_entra_lifecycle_reconcile import (
    FORMAT, ProbeError, _graph_origin, _graph_token, _manifest, main, reconcile,
)
from test_process_collaboration import store  # noqa: F401 (http fixture)
from test_scim_routes import http, user  # noqa: F401
from test_scim_lifecycle_readback_probe import _create, _deactivate
from test_scim_readonly_rollout_probe import _ActualASGIRoutes
from test_scim_provider_isolation_probe import _tcp_scim

TENANT = "11111111-2222-3333-4444-555555555555"
ENTRA_1 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ENTRA_2 = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


def _jwt(tenant=TENANT):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return part({"alg": "RS256", "typ": "JWT"}) + "." + part({
        "tid": tenant, "aud": "https://graph.microsoft.com", "test_only": "x" * 50,
    }) + ".disposable-signature"


def _manifest_bytes(provider, *users, tenant=TENANT):
    return json.dumps({
        "format": FORMAT, "provider_id": provider, "entra_tenant_id": tenant,
        "users": [{"entra_id": eid, "owg_id": oid, "expected_active": active}
                  for eid, oid, active in users],
    }).encode()


@contextmanager
def _graph_server(states, *, failure=None, change_after=None):
    """Disposable actual TCP HTTP Graph-shaped GETs, never a real IdP."""
    token = _jwt()
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + token:
                self.send_response(401)
                self.end_headers()
                return
            if failure == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/steal")
                self.end_headers()
                return
            if failure == "unauthorized":
                self.send_response(401)
                self.end_headers()
                return
            if failure == "rate_limit":
                self.send_response(429)
                self.send_header("Retry-After", "1")
                self.end_headers()
                return
            parsed = parse.urlsplit(self.path)
            expected_query = parse.parse_qs(parsed.query)
            if expected_query != {"$select": ["id,accountEnabled"]}:
                self.send_response(400)
                self.end_headers()
                return
            eid = parsed.path.removeprefix("/v1.0/users/")
            if not parsed.path.startswith("/v1.0/users/") or eid not in states:
                self.send_response(404)
                self.end_headers()
                return
            enabled = states[eid]
            if change_after is not None and len(calls) >= change_after:
                enabled = not enabled
            payload = {"@odata.context": "test-only", "id": eid,
                       "accountEnabled": enabled}
            if failure == "wrong_id":
                payload["id"] = ENTRA_2 if eid == ENTRA_1 else ENTRA_1
            if failure == "missing_state":
                del payload["accountEnabled"]
            if failure == "wrong_type":
                payload["accountEnabled"] = "true"
            if failure == "duplicate_json":
                body = b'{"id":"x","id":"y","accountEnabled":true}'
            elif failure == "oversize":
                body = json.dumps({**payload, "unused": "x" * 70000}).encode()
            elif failure == "invalid_json":
                body = b'{"accountEnabled":'
            else:
                body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json;odata.metadata=minimal")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            pytest.fail("Read-only Graph reconciliation attempted POST")
        do_PATCH = do_POST
        do_DELETE = do_POST
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), token, calls
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _run(http, graph_origin, token, raw, *, owg_opener=None, owg_origin=None):
    client, db, owner, headers, base, pid = http
    return reconcile(
        graph_origin, (owg_origin or "https://owg.example.test") + base,
        "127.0.0.1" if owg_origin else "owg.example.test",
        pid, TENANT, token, headers["Authorization"][7:], raw,
        allow_loopback_http=True,
        owg_opener=owg_opener if owg_opener is not None else _ActualASGIRoutes(client),
    )


def test_actual_owg_routes_and_real_graph_tcp_match_active_and_deactivated(http):
    client, db, owner, headers, base, pid = http
    active = _create(client, base, headers)
    inactive = _create(client, base, headers, name="retired@acme.test")
    retired_id = _deactivate(client, base, headers, inactive)
    raw = _manifest_bytes(pid, (ENTRA_1, active.json()["id"], True),
                          (ENTRA_2, retired_id, False))
    owg = _ActualASGIRoutes(client)
    with _graph_server({ENTRA_1: True, ENTRA_2: False}) as (origin, token, calls):
        result = _run(http, origin, token, raw, owg_opener=owg)
    assert result["status"] == "selected_lifecycle_states_match"
    assert result["users_checked"] == 2 and result["http_gets"] == 7
    assert len(calls) == 3 and len(owg.requests) == 4
    assert all(method == "GET" and auth == "Bearer " + token
               for method, _, auth in calls)
    assert all("/Users?" not in url and "/Groups" not in url for url in owg.requests)
    assert result["provider_writes_performed"] is False
    assert result["snapshot_consistency"] == "not_atomic"
    serialized = json.dumps(result)
    for private in (ENTRA_1, ENTRA_2, active.json()["id"], retired_id,
                    active.json()["userName"], pid, token,
                    headers["Authorization"][7:]):
        assert private not in serialized


def test_actual_owg_routes_and_graph_over_separate_real_tcp_servers(http):
    client, db, owner, headers, base, pid = http
    uid = _create(client, base, headers).json()["id"]
    with _graph_server({ENTRA_1: True}) as (origin, token, graph_calls):
        with _tcp_scim(client) as (owg_origin, owg_calls):
            result = _run(http, origin, token,
                          _manifest_bytes(pid, (ENTRA_1, uid, True)),
                          owg_origin=owg_origin)
    assert result["http_gets"] == 5
    assert len(graph_calls) == 2 and len(owg_calls) == 3


@pytest.mark.parametrize("failure", [
    "redirect", "unauthorized", "rate_limit", "wrong_id", "missing_state",
    "wrong_type", "duplicate_json", "oversize", "invalid_json",
])
def test_real_graph_tcp_failures_refuse_without_retries(http, failure):
    client, db, owner, headers, base, pid = http
    uid = _create(client, base, headers).json()["id"]
    with _graph_server({ENTRA_1: True}, failure=failure) as (origin, token, calls):
        with pytest.raises(ProbeError):
            _run(http, origin, token, _manifest_bytes(pid, (ENTRA_1, uid, True)))
    assert len(calls) == 1
    assert all(method == "GET" for method, _, _ in calls)


def test_cross_system_disagreement_refuses(http):
    client, db, owner, headers, base, pid = http
    uid = _create(client, base, headers).json()["id"]
    with _graph_server({ENTRA_1: False}) as (origin, token, calls):
        with pytest.raises(ProbeError, match="lifecycle"):
            _run(http, origin, token, _manifest_bytes(pid, (ENTRA_1, uid, True)))
    assert len(calls) == 1


def test_final_graph_read_detects_changed_state(http):
    client, db, owner, headers, base, pid = http
    uid = _create(client, base, headers).json()["id"]
    with _graph_server({ENTRA_1: True}, change_after=2) as (origin, token, calls):
        with pytest.raises(ProbeError, match="changed"):
            _run(http, origin, token, _manifest_bytes(pid, (ENTRA_1, uid, True)))
    assert len(calls) == 2


def test_owg_revocation_during_cross_system_read_refuses(http):
    client, db, owner, headers, base, pid = http
    uid = _create(client, base, headers).json()["id"]
    class Revoking(_ActualASGIRoutes):
        def open(self, req, timeout):
            result = super().open(req, timeout)
            if len(self.requests) == 2:
                response = client.post(
                    "/v1/admin/scim-providers/acme/" + pid + "/revoke",
                    headers=owner, json={"revision": 2})
                assert response.status_code == 200
            return result
    with _graph_server({ENTRA_1: True}) as (origin, token, calls):
        with pytest.raises(ProbeError):
            _run(http, origin, token, _manifest_bytes(pid, (ENTRA_1, uid, True)),
                 owg_opener=Revoking(client))
    assert len(calls) == 2


@pytest.mark.parametrize("raw", [
    b"{}", b"invalid", b'{"format":1,"format":2}',
    b'{"format":NaN}', b"[" + b"0," * 9000 + b"0]",
])
def test_bad_private_manifest_fails_before_network(raw):
    with pytest.raises(ProbeError):
        _manifest(raw, "provider", TENANT)


def test_duplicate_mapping_foreign_tenant_and_provider_refuse(http):
    client, db, owner, headers, base, pid = http
    uid = _create(client, base, headers).json()["id"]
    duplicates = _manifest_bytes(pid, (ENTRA_1, uid, True), (ENTRA_1, "other", True))
    foreign = _manifest_bytes("other", (ENTRA_1, uid, True))
    tenant = _manifest_bytes(pid, (ENTRA_1, uid, True),
                             tenant="22222222-2222-3333-4444-555555555555")
    for raw in (duplicates, foreign, tenant):
        class Forbidden:
            def open(self, *args, **kwargs):
                pytest.fail("Preflight must not contact source")
        with pytest.raises(ProbeError):
            reconcile("http://127.0.0.1:1234",
                      "https://owg.example.test" + base, "owg.example.test",
                      pid, TENANT, _jwt(), headers["Authorization"][7:], raw,
                      allow_loopback_http=True,
                      graph_opener=Forbidden(), owg_opener=Forbidden())


def test_unconfirmed_graph_origin_or_wrong_tenant_token_refuses(http):
    client, db, owner, headers, base, pid = http
    uid = _create(client, base, headers).json()["id"]
    raw = _manifest_bytes(pid, (ENTRA_1, uid, True))
    class Forbidden:
        def open(self, *args, **kwargs):
            pytest.fail("Unsafe origin or tenant must not contact source")
    for origin, token in (
        ("https://example.test", _jwt()),
        ("http://graph.microsoft.com", _jwt()),
        ("https://graph.microsoft.com:444", _jwt()),
        ("https://graph.microsoft.com@evil.test", _jwt()),
        ("https://graph.microsoft.com", _jwt("99999999-2222-3333-4444-555555555555")),
    ):
        with pytest.raises(ProbeError):
            reconcile(origin, "https://owg.example.test" + base,
                      "owg.example.test", pid, TENANT, token,
                      headers["Authorization"][7:], raw,
                      graph_opener=Forbidden(), owg_opener=Forbidden())


def test_cli_three_consents_required_and_redacted_aggregate(http, monkeypatch, capsys):
    client, db, owner, headers, base, pid = http
    uid = _create(client, base, headers).json()["id"]
    raw = _manifest_bytes(pid, (ENTRA_1, uid, True))
    args = ["--owg-base-url", "https://owg.example.test" + base,
            "--confirm-owg-host", "owg.example.test",
            "--confirm-provider", pid, "--confirm-tenant", TENANT,
            "--allow-loopback-http"]
    with _graph_server({ENTRA_1: True}) as (origin, token, calls):
        monkeypatch.setenv("OWG_ENTRA_READ_TOKEN", token)
        monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN", headers["Authorization"][7:])
        args += ["--graph-origin", origin]
        owg = _ActualASGIRoutes(client)
        assert main(args, stdin=io.BytesIO(raw), owg_opener=owg) == 2
        assert calls == [] and owg.requests == []
        code = main(args + ["--consent-entra-read", "--consent-owg-read",
                            "--consent-cross-system-reconcile"],
                    stdin=io.BytesIO(raw), owg_opener=owg)
    assert code == 0
    receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert receipt["users_checked"] == 1 and receipt["http_gets"] == 5
    for secret in (uid, ENTRA_1, pid, TENANT, token):
        assert secret not in json.dumps(receipt)
