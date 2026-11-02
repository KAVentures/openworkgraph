"""Live Graph TCP + production OWG FastAPI/TCP direct-user Group parity tests."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from threading import Thread
from urllib import parse

import pytest

from scripts.scim_entra_group_full_readback import (
    FORMAT, ProbeError, _manifest, main, reconcile_full_groups,
)
from test_process_collaboration import store  # noqa: F401
from test_scim_routes import http, user  # noqa: F401
from test_scim_entra_lifecycle_reconcile import ENTRA_1, ENTRA_2, TENANT, _jwt
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
def _graph_server(states, memberships, *, failure=None, mutate_second_group=False):
    """Disposable Graph-shaped real TCP transport, never Microsoft Graph."""
    token = _jwt()
    calls = []
    group_reads = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + token or failure == "401":
                self.send_response(401)
                self.end_headers()
                return
            if failure == "429":
                self.send_response(429)
                self.end_headers()
                return
            if failure == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/leak")
                self.end_headers()
                return
            parsed = parse.urlsplit(self.path)
            query = parse.parse_qs(parsed.query)
            group_prefix = "/v1.0/groups/"
            user_prefix = "/v1.0/users/"
            is_group = (parsed.path.startswith(group_prefix)
                        and parsed.path.endswith("/members/microsoft.graph.user"))
            if is_group:
                gid = parsed.path[len(group_prefix):].removesuffix(
                    "/members/microsoft.graph.user")
                expected_query = {"$select": ["id"], "$top": ["100"], "$count": ["true"]}
                if (gid not in memberships or query != expected_query
                        or self.headers.get("ConsistencyLevel") != "eventual"):
                    self.send_response(404)
                    self.end_headers()
                    return
                group_reads.append(gid)
                members = list(memberships[gid])
                if mutate_second_group and len(group_reads) >= 2:
                    members = []
                payload = {"value": [{"id": uid} for uid in members],
                           "@odata.count": len(members)}
                if failure == "nextlink":
                    payload["@odata.nextLink"] = "https://graph.microsoft.com/next"
                elif failure == "missing_count":
                    del payload["@odata.count"]
                elif failure == "wrong_count":
                    payload["@odata.count"] += 1
                elif failure == "bool_count":
                    payload["@odata.count"] = True
                elif failure == "duplicate":
                    payload["value"] += payload["value"]
                    payload["@odata.count"] = len(payload["value"])
                elif failure == "bad_type":
                    payload["value"] = [{"id": members[0],
                                         "@odata.type": "#microsoft.graph.group"}]
                elif failure == "unknown":
                    payload["value"] = [{"id": GROUP_2}]
                    payload["@odata.count"] = 1
            elif parsed.path.startswith(user_prefix):
                uid = parsed.path[len(user_prefix):]
                if uid not in states or query != {"$select": ["id,accountEnabled"]}:
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = {"id": uid, "accountEnabled": states[uid]}
            else:
                self.send_response(404)
                self.end_headers()
                return
            if failure == "duplicate_json" and is_group:
                body = b'{"value":[],"value":[],"@odata.count":0}'
            elif failure == "oversize" and is_group:
                body = json.dumps({**payload, "ignored": "x" * 70000}).encode()
            else:
                body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json;odata.metadata=minimal")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            calls.append(("POST", self.path, None))
            self.send_response(405)
            self.end_headers()
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


def _fixture(http, *, extra_owg_member=False):
    client, db, owner, headers, base, pid = http
    u1 = _create_user(client, base, headers, "complete-first@acme.test")
    u2 = _create_user(client, base, headers, "complete-second@acme.test")
    extra = (_create_user(client, base, headers, "unmapped-third@acme.test")
             if extra_owg_member else None)
    gid = _create_group(client, base, headers, [u1, u2] + ([extra] if extra else []))
    raw = _payload(pid, [(ENTRA_1, u1, True), (ENTRA_2, u2, True)],
                   [(GROUP_1, gid, [ENTRA_1, ENTRA_2])])
    return raw, u1, u2, gid


def _run(http, origin, token, raw, *, owg_opener=None, owg_origin=None):
    client, db, owner, headers, base, pid = http
    return reconcile_full_groups(
        origin, (owg_origin or "https://owg.example.test") + base,
        "127.0.0.1" if owg_origin else "owg.example.test",
        pid, TENANT, token, headers["Authorization"][7:], raw,
        allow_loopback_http=True,
        owg_opener=(owg_opener if owg_opener is not None
                    else (None if owg_origin else _ActualASGIRoutes(client))),
    )


def test_complete_group_parity_real_graph_tcp_actual_scim_routes(http):
    raw, u1, u2, gid = _fixture(http)
    client = http[0]
    owg = _ActualASGIRoutes(client)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {GROUP_1: [ENTRA_1, ENTRA_2]}) as (origin, token, calls):
        result = _run(http, origin, token, raw, owg_opener=owg)
    assert result["status"] == "selected_groups_direct_user_membership_match"
    assert result["http_gets"] == 10
    assert len(calls) == 4 and len(owg.requests) == 6
    assert all(method == "GET" and auth == "Bearer " + token
               for method, _, auth in calls)
    assert result["non_user_and_nested_membership"] == "not_verified"
    assert result["provider_writes_performed"] is False
    for secret in (u1, u2, gid, http[5], ENTRA_1, ENTRA_2, GROUP_1,
                   TENANT, token, http[3]["Authorization"][7:]):
        assert secret not in json.dumps(result)


def test_both_services_use_real_loopback_tcp(http):
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {GROUP_1: [ENTRA_1, ENTRA_2]}) as (origin, token, calls):
        with _tcp_scim(http[0]) as (owg_origin, owg_calls):
            receipt = _run(http, origin, token, raw, owg_origin=owg_origin)
    assert receipt["http_gets"] == 10
    assert len(calls) == 4 and len(owg_calls) == 6


@pytest.mark.parametrize("failure", [
    "401", "429", "redirect", "nextlink", "missing_count", "wrong_count",
    "bool_count", "duplicate", "bad_type", "unknown", "duplicate_json", "oversize",
])
def test_graph_refusals_are_bounded_and_never_retried(http, failure):
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {GROUP_1: [ENTRA_1, ENTRA_2]},
                       failure=failure) as (origin, token, calls):
        with pytest.raises(ProbeError):
            _run(http, origin, token, raw)
    assert len(calls) == 1
    assert all(method == "GET" for method, _, _ in calls)


def test_extra_owg_group_member_refuses_full_parity(http):
    raw, u1, u2, gid = _fixture(http, extra_owg_member=True)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {GROUP_1: [ENTRA_1, ENTRA_2]}) as (origin, token, calls):
        with pytest.raises(ProbeError, match="OWG full"):
            _run(http, origin, token, raw)
    assert len(calls) == 1


def test_unmapped_graph_group_member_refuses_full_parity(http):
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {GROUP_1: [ENTRA_1]}) as (origin, token, calls):
        with pytest.raises(ProbeError, match="Entra full"):
            _run(http, origin, token, raw)
    assert len(calls) == 1


def test_graph_group_drift_on_final_readback_refuses(http):
    raw, u1, u2, gid = _fixture(http)
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {GROUP_1: [ENTRA_1, ENTRA_2]},
                       mutate_second_group=True) as (origin, token, calls):
        with pytest.raises(ProbeError, match="changed"):
            _run(http, origin, token, raw)
    assert len(calls) == 4


def test_revoked_owg_provider_fails_closed(http):
    client, db, owner, headers, base, pid = http
    raw, u1, u2, gid = _fixture(http)

    class Revoking(_ActualASGIRoutes):
        def open(self, req, timeout):
            result = super().open(req, timeout)
            if len(self.requests) == 2:
                response = client.post(
                    "/v1/admin/scim-providers/acme/" + pid + "/revoke",
                    headers=owner, json={"revision": 2})
                assert response.status_code == 200
            return result

    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {GROUP_1: [ENTRA_1, ENTRA_2]}) as (origin, token, calls):
        with pytest.raises(ProbeError):
            _run(http, origin, token, raw, owg_opener=Revoking(client))


def test_manifest_scope_and_duplicates_refuse_before_network(http):
    raw, u1, u2, gid = _fixture(http)
    pid = http[5]
    bad = [
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

    class Forbidden:
        def open(self, *args, **kwargs):
            pytest.fail("Preflight must not contact either service")

    for invalid in bad:
        with pytest.raises(ProbeError):
            reconcile_full_groups(
                "http://127.0.0.1:1234", "https://owg.example.test" + http[4],
                "owg.example.test", pid, TENANT, _jwt(),
                http[3]["Authorization"][7:], invalid,
                allow_loopback_http=True,
                graph_opener=Forbidden(), owg_opener=Forbidden())


def test_cli_three_consents_aggregate_receipt(http, monkeypatch, capsys):
    raw, u1, u2, gid = _fixture(http)
    args = [
        "--owg-base-url", "https://owg.example.test" + http[4],
        "--confirm-owg-host", "owg.example.test",
        "--confirm-provider", http[5], "--confirm-tenant", TENANT,
        "--allow-loopback-http",
    ]
    with _graph_server({ENTRA_1: True, ENTRA_2: True},
                       {GROUP_1: [ENTRA_1, ENTRA_2]}) as (origin, token, calls):
        monkeypatch.setenv("OWG_ENTRA_READ_TOKEN", token)
        monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN", http[3]["Authorization"][7:])
        args += ["--graph-origin", origin]
        owg = _ActualASGIRoutes(http[0])
        assert main(args, stdin=io.BytesIO(raw), owg_opener=owg) == 2
        assert calls == [] and owg.requests == []
        assert main(args + [
            "--consent-entra-group-read", "--consent-owg-group-read",
            "--consent-cross-system-reconcile",
        ], stdin=io.BytesIO(raw), owg_opener=owg) == 0
    receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert receipt["users_checked"] == 2 and receipt["groups_checked"] == 1
    for secret in (u1, u2, gid, http[5], ENTRA_1, GROUP_1, TENANT, token):
        assert secret not in json.dumps(receipt)
