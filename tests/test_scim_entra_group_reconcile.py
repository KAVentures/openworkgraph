"""Real TCP Graph-shaped + actual OWG FastAPI SCIM selected direct-group tests."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from threading import Thread
from urllib import parse

import pytest

from scripts.scim_entra_group_reconcile import (
    FORMAT, ProbeError, _manifest, main, reconcile_groups,
)
from test_process_collaboration import store  # noqa: F401
from test_scim_routes import http, user  # noqa: F401
from test_scim_entra_lifecycle_reconcile import (
    ENTRA_1, ENTRA_2, TENANT, _jwt,
)
from test_scim_group_membership_readback_probe import _create_group, _create_user
from test_scim_provider_isolation_probe import _tcp_scim
from test_scim_readonly_rollout_probe import _ActualASGIRoutes

GROUP_1 = "cccccccc-dddd-eeee-ffff-000000000001"
GROUP_2 = "cccccccc-dddd-eeee-ffff-000000000002"


def _payload(provider, users, groups, tenant=TENANT):
    return json.dumps({
        "format": FORMAT, "provider_id": provider, "entra_tenant_id": tenant,
        "users": [{"entra_id": eid, "owg_id": oid, "expected_active": active}
                  for eid, oid, active in users],
        "groups": [{"entra_id": eid, "owg_id": oid, "member_entra_ids": members}
                   for eid, oid, members in groups],
    }).encode()


@contextmanager
def _graph_server(states, memberships, *, failure=None, mutate_after=None):
    """Disposable real TCP Graph GET server; no real Microsoft service involved."""
    token = _jwt()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + token or failure == "401":
                self.send_response(401)
                self.end_headers()
                return
            if failure == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/leak")
                self.end_headers()
                return
            if failure == "429":
                self.send_response(429)
                self.end_headers()
                return
            parsed = parse.urlsplit(self.path)
            prefix = "/v1.0/users/"
            if not parsed.path.startswith(prefix):
                self.send_response(404)
                self.end_headers()
                return
            rest = parsed.path[len(prefix):]
            direct = rest.endswith("/memberOf/microsoft.graph.group")
            eid = rest.removesuffix("/memberOf/microsoft.graph.group") if direct else rest
            query = parse.parse_qs(parsed.query)
            expected = ({"$select": ["id"], "$top": ["100"], "$count": ["true"]} if direct
                        else {"$select": ["id,accountEnabled"]})
            if eid not in states or query != expected or (direct and self.headers.get("ConsistencyLevel") != "eventual"):
                self.send_response(404)
                self.end_headers()
                return
            if direct:
                groups = list(memberships.get(eid, ()))
                if mutate_after is not None and sum(
                        "/memberOf/" in c[1] for c in calls) >= mutate_after:
                    groups = []
                payload = {"value": [{"id": gid} for gid in groups]}
                if failure == "nextlink":
                    payload["@odata.nextLink"] = (
                        "https://graph.microsoft.com/v1.0/users/" + eid
                        + "/memberOf?$skiptoken=unverified")
                if failure == "duplicate_group":
                    payload["value"] += payload["value"]
                if failure == "wrong_type":
                    payload["value"] = [{"id": 123}]
            else:
                payload = {"id": eid, "accountEnabled": states[eid]}
            if failure == "duplicate_json" and direct:
                body = b'{"value":[],"value":[]}'
            elif failure == "oversize" and direct:
                body = json.dumps({**payload, "ignored": "x" * 70000}).encode()
            else:
                body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json;odata.metadata=minimal")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            pytest.fail("Read-only Graph reconciliation must not POST")
        do_PATCH = do_POST
        do_DELETE = do_POST
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), token, calls
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _fixture(http):
    client, db, owner, headers, base, pid = http
    u1 = _create_user(client, base, headers, "selected-first@acme.test")
    u2 = _create_user(client, base, headers, "selected-second@acme.test")
    gid = _create_group(client, base, headers, [u1])
    raw = _payload(pid, [(ENTRA_1, u1, True), (ENTRA_2, u2, True)],
                   [(GROUP_1, gid, [ENTRA_1])])
    return raw, u1, u2, gid


def _run(http, origin, token, raw, *, owg_opener=None, owg_origin=None):
    client, db, owner, headers, base, pid = http
    return reconcile_groups(
        origin, (owg_origin or "https://owg.example.test") + base,
        "127.0.0.1" if owg_origin else "owg.example.test",
        pid, TENANT, token, headers["Authorization"][7:], raw,
        allow_loopback_http=True,
        owg_opener=owg_opener if owg_opener is not None else _ActualASGIRoutes(client),
    )


def test_selected_direct_memberships_live_graph_tcp_and_actual_scim_routes(http):
    client, db, owner, headers, base, pid = http
    raw, u1, u2, gid = _fixture(http)
    owg = _ActualASGIRoutes(client)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {ENTRA_1: [GROUP_1], ENTRA_2: []}) as (origin, token, calls):
        result = _run(http, origin, token, raw, owg_opener=owg)
    assert result["status"] == "selected_direct_memberships_match"
    assert result["http_gets"] == 12  # 3*2 + 1 + 5
    assert len(calls) == 6 and len(owg.requests) == 6
    assert all(method == "GET" and auth == "Bearer " + token
               for method, _, auth in calls)
    assert result["whole_group_parity"] == "not_verified"
    assert result["nested_transitive_memberships"] == "not_verified"
    assert result["provider_writes_performed"] is False
    for secret in (u1, u2, gid, pid, ENTRA_1, ENTRA_2, GROUP_1, TENANT,
                   token, headers["Authorization"][7:]):
        assert secret not in json.dumps(result)


def test_graph_and_scim_over_two_actual_tcp_transports(http):
    client, db, owner, headers, base, pid = http
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {ENTRA_1: [GROUP_1], ENTRA_2: []}) as (origin, token, calls):
        with _tcp_scim(client) as (owg_origin, owg_calls):
            receipt = _run(http, origin, token, raw, owg_origin=owg_origin)
    assert receipt["http_gets"] == 12
    assert len(calls) == 6 and len(owg_calls) == 6


@pytest.mark.parametrize("failure", [
    "401", "redirect", "429", "nextlink", "duplicate_group",
    "wrong_type", "duplicate_json", "oversize",
])
def test_real_graph_failures_refuse_without_retry(http, failure):
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {ENTRA_1: [GROUP_1], ENTRA_2: []},
                       failure=failure) as (origin, token, calls):
        with pytest.raises(ProbeError):
            _run(http, origin, token, raw)
    assert len(calls) <= 2
    assert all(c[0] == "GET" for c in calls)


def test_cross_system_selected_membership_mismatch_refuses(http):
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {ENTRA_1: [], ENTRA_2: []}) as (origin, token, calls):
        with pytest.raises(ProbeError, match="membership"):
            _run(http, origin, token, raw)
    assert len(calls) == 2


def test_unselected_group_is_not_misreported_as_complete_group_parity(http):
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {ENTRA_1: [GROUP_1, GROUP_2], ENTRA_2: [GROUP_2]}) as (
            origin, token, calls):
        result = _run(http, origin, token, raw)
    assert result["status"] == "selected_direct_memberships_match"
    assert result["whole_group_parity"] == "not_verified"


def test_graph_membership_change_during_readback_refuses(http):
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {ENTRA_1: [GROUP_1], ENTRA_2: []},
                       mutate_after=3) as (origin, token, calls):
        with pytest.raises(ProbeError, match="changed"):
            _run(http, origin, token, raw)
    assert len(calls) == 6


def test_owg_provider_revocation_mid_readback_refuses(http):
    client, db, owner, headers, base, pid = http
    raw, u1, u2, gid = _fixture(http)

    class Revoking(_ActualASGIRoutes):
        def open(self, req, timeout):
            result = super().open(req, timeout)
            if len(self.requests) == 2:
                revoked = client.post(
                    "/v1/admin/scim-providers/acme/" + pid + "/revoke",
                    headers=owner, json={"revision": 2})
                assert revoked.status_code == 200
            return result

    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {ENTRA_1: [GROUP_1], ENTRA_2: []}) as (origin, token, calls):
        with pytest.raises(ProbeError):
            _run(http, origin, token, raw, owg_opener=Revoking(client))


def test_private_manifest_preflight_refuses_before_any_network(http):
    client, db, owner, headers, base, pid = http
    raw, u1, u2, gid = _fixture(http)
    cases = [
        b"{}", b"not-json", b'{"format":1,"format":2}',
        _payload("other", [(ENTRA_1, u1, True)], [(GROUP_1, gid, [ENTRA_1])]),
        _payload(pid, [(ENTRA_1, u1, True), (ENTRA_1, u2, True)],
                 [(GROUP_1, gid, [ENTRA_1])]),
        _payload(pid, [(ENTRA_1, u1, False)], [(GROUP_1, gid, [ENTRA_1])]),
        _payload(pid, [(ENTRA_1, u1, True)], [(GROUP_1, gid, [ENTRA_2])]),
        _payload(pid, [(ENTRA_1, u1, True)], [(GROUP_1, gid, [ENTRA_1, ENTRA_1])]),
        _payload(pid, [(ENTRA_1, u1, True)], [(GROUP_1, gid, [ENTRA_1])],
                 tenant="22222222-2222-3333-4444-555555555555"),
    ]
    for invalid in cases:
        class Forbidden:
            def open(self, *args, **kwargs):
                pytest.fail("Preflight must not contact Graph or OWG")
        with pytest.raises(ProbeError):
            reconcile_groups(
                "http://127.0.0.1:1234", "https://owg.example.test" + base,
                "owg.example.test", pid, TENANT, _jwt(),
                headers["Authorization"][7:], invalid,
                allow_loopback_http=True,
                graph_opener=Forbidden(), owg_opener=Forbidden())


def test_cli_requires_three_consents_and_emits_aggregate_only(http, monkeypatch, capsys):
    client, db, owner, headers, base, pid = http
    raw, u1, u2, gid = _fixture(http)
    args = [
        "--owg-base-url", "https://owg.example.test" + base,
        "--confirm-owg-host", "owg.example.test",
        "--confirm-provider", pid, "--confirm-tenant", TENANT,
        "--allow-loopback-http",
    ]
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {ENTRA_1: [GROUP_1], ENTRA_2: []}) as (origin, token, calls):
        monkeypatch.setenv("OWG_ENTRA_READ_TOKEN", token)
        monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN", headers["Authorization"][7:])
        args += ["--graph-origin", origin]
        owg = _ActualASGIRoutes(client)
        assert main(args, stdin=io.BytesIO(raw), owg_opener=owg) == 2
        assert calls == [] and owg.requests == []
        assert main(args + [
            "--consent-entra-membership-read", "--consent-owg-membership-read",
            "--consent-cross-system-reconcile",
        ], stdin=io.BytesIO(raw), owg_opener=owg) == 0
    receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert receipt["users_checked"] == 2 and receipt["groups_checked"] == 1
    for secret in (u1, u2, gid, pid, ENTRA_1, GROUP_1, TENANT, token):
        assert secret not in json.dumps(receipt)
