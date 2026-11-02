"""Read-only SCIM Group membership acceptance against production OWG routes/TCP."""
from __future__ import annotations
import io
import json

import pytest

from scripts.scim_group_membership_readback_probe import (
    FORMAT, ProbeError, _manifest, membership_readback, main,
)
from test_process_collaboration import store  # noqa: F401
from test_scim_routes import http, user  # noqa: F401
from test_scim_readonly_rollout_probe import _ActualASGIRoutes
from test_scim_provider_isolation_probe import _tcp_scim


def _payload(provider, users, groups):
    return json.dumps({
        "format": FORMAT, "provider_id": provider,
        "users": [{"id": uid, "expected_active": active} for uid, active in users],
        "groups": [{"id": gid, "members": members} for gid, members in groups],
    }).encode()


def _create_user(client, base, headers, name):
    body = user()
    body["userName"] = name
    body["externalId"] = name
    response = client.post(base + "/Users", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_group(client, base, headers, members):
    response = client.post(base + "/Groups", headers=headers, json={
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": "Disposable reviewers",
        "members": [{"value": uid, "type": "User"} for uid in members],
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _read(http, raw, *, opener=None):
    client, db, owner, headers, base, pid = http
    return membership_readback(
        "https://owg.example.test" + base, "owg.example.test", pid,
        headers["Authorization"][7:], raw,
        opener=opener if opener is not None else _ActualASGIRoutes(client))


def test_real_scim_routes_verify_group_and_reciprocal_user_views(http):
    client, db, owner, headers, base, pid = http
    first = _create_user(client, base, headers, "first@acme.test")
    second = _create_user(client, base, headers, "second@acme.test")
    gid = _create_group(client, base, headers, [first])
    opener = _ActualASGIRoutes(client)
    receipt = _read(http, _payload(pid, [(first, True), (second, True)],
                                   [(gid, [first])]), opener=opener)
    assert receipt["status"] == "selected_group_membership_verified"
    assert receipt["groups_checked"] == 1 and receipt["users_checked"] == 2
    assert receipt["http_gets"] == 5 and len(opener.requests) == 5
    assert receipt["owg_roles_teams_sso_permissions"] == "not_verified"
    assert receipt["provider_writes_performed"] is False
    serialized = json.dumps(receipt)
    for secret in (first, second, gid, pid, headers["Authorization"][7:],
                   "first@acme.test", "second@acme.test"):
        assert secret not in serialized
    assert client.get("/v1/human/processes", headers=headers).status_code == 401


def test_real_scim_routes_detect_missing_or_unexpected_member(http):
    client, db, owner, headers, base, pid = http
    uid = _create_user(client, base, headers, "first@acme.test")
    gid = _create_group(client, base, headers, [uid])
    with pytest.raises(ProbeError):
        _read(http, _payload(pid, [(uid, True)], [(gid, [])]))
    missing = "grp_" + "0" * 32
    with pytest.raises(ProbeError):
        _read(http, _payload(pid, [(uid, True)], [(missing, [])]))


def test_real_scim_routes_detect_group_change_between_readback_and_user(http):
    client, db, owner, headers, base, pid = http
    uid = _create_user(client, base, headers, "first@acme.test")
    gid = _create_group(client, base, headers, [uid])

    class ChangeAfterGroup(_ActualASGIRoutes):
        def open(self, req, timeout):
            result = super().open(req, timeout)
            if len(self.requests) == 2:
                group = client.get(base + "/Groups/" + gid, headers=headers)
                removed = client.patch(
                    base + "/Groups/" + gid,
                    headers={**headers, "If-Match": group.headers["etag"]},
                    json={"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                          "Operations": [{"op": "remove", "path": "members"}]},
                )
                assert removed.status_code == 200, removed.text
            return result

    with pytest.raises(ProbeError, match="Reciprocal"):
        _read(http, _payload(pid, [(uid, True)], [(gid, [uid])]),
              opener=ChangeAfterGroup(client))


def test_real_scim_routes_revoke_during_readback_refuses(http):
    client, db, owner, headers, base, pid = http
    uid = _create_user(client, base, headers, "first@acme.test")
    gid = _create_group(client, base, headers, [uid])

    class RevokeAfterGroup(_ActualASGIRoutes):
        def open(self, req, timeout):
            result = super().open(req, timeout)
            if len(self.requests) == 2:
                revoked = client.post(
                    "/v1/admin/scim-providers/acme/" + pid + "/revoke",
                    headers=owner, json={"revision": 2})
                assert revoked.status_code == 200, revoked.text
            return result

    with pytest.raises(ProbeError):
        _read(http, _payload(pid, [(uid, True)], [(gid, [uid])]),
              opener=RevokeAfterGroup(client))


def test_real_scim_routes_over_disposable_tcp(http):
    client, db, owner, headers, base, pid = http
    uid = _create_user(client, base, headers, "first@acme.test")
    gid = _create_group(client, base, headers, [uid])
    with _tcp_scim(client) as (origin, calls):
        result = membership_readback(
            origin + base, "127.0.0.1", pid, headers["Authorization"][7:],
            _payload(pid, [(uid, True)], [(gid, [uid])]),
            allow_loopback_http=True)
    assert result["http_gets"] == 4
    assert len(calls) == 4
    assert all("/Groups?" not in path and "/Users?" not in path for path in calls)


@pytest.mark.parametrize("raw", [
    b"{}", b"not-json", b'{"format":1,"format":2}',
    b'{"format":NaN}', b"[" + b"0," * 9000 + b"0]",
])
def test_bad_manifest_refuses(raw):
    with pytest.raises(ProbeError):
        _manifest(raw, "p")


def test_contradictory_and_duplicate_selection_refuses_before_network(http):
    client, db, owner, headers, base, pid = http
    opener = _ActualASGIRoutes(client)
    cases = [
        _payload(pid, [("u", False)], [("grp_" + "0"*32, ["u"])]),
        _payload(pid, [("u", True)], [("grp_" + "0"*32, ["u", "u"])]),
        _payload(pid, [("u", True), ("u", False)], [("grp_" + "0"*32, [])]),
        _payload("foreign", [("u", True)], [("grp_" + "0"*32, [])]),
    ]
    for raw in cases:
        with pytest.raises(ProbeError):
            _read(http, raw, opener=opener)
    assert opener.requests == []


def test_cli_requires_consent_and_only_emits_aggregate(http, monkeypatch, capsys):
    client, db, owner, headers, base, pid = http
    uid = _create_user(client, base, headers, "first@acme.test")
    gid = _create_group(client, base, headers, [uid])
    raw = _payload(pid, [(uid, True)], [(gid, [uid])])
    opener = _ActualASGIRoutes(client)
    monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN", headers["Authorization"][7:])
    args = ["--base-url", "https://owg.example.test" + base,
            "--confirm-host", "owg.example.test", "--confirm-provider", pid]
    assert main(args, opener=opener, stdin=io.BytesIO(raw)) == 2
    assert opener.requests == []
    assert main(args + ["--consent-membership-read"],
                opener=opener, stdin=io.BytesIO(raw)) == 0
    receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert receipt["groups_checked"] == 1
    assert uid not in json.dumps(receipt) and gid not in json.dumps(receipt)
    assert len(opener.requests) == 4
