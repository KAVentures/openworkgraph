"""Live SCIM lifecycle readback acceptance against actual OWG routes and TCP."""
from __future__ import annotations

import io
import json

import pytest

from scripts.scim_lifecycle_readback_probe import (
    FORMAT, ProbeError, _manifest, lifecycle_readback, main,
)
from test_process_collaboration import store  # noqa: F401 (http fixture dependency)
from test_scim_routes import http, user  # noqa: F401 (pytest fixtures)
from test_scim_readonly_rollout_probe import _ActualASGIRoutes
from test_scim_provider_isolation_probe import _tcp_scim


def _payload(provider, *entries):
    return json.dumps({
        "format": FORMAT, "provider_id": provider,
        "users": [{"id": identifier, "expected_active": active}
                  for identifier, active in entries],
    }).encode()


def _create(client, base, headers, *, name="person@acme.test"):
    body = user()
    body["userName"] = name
    body["externalId"] = name
    response = client.post(base + "/Users", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response


def _deactivate(client, base, headers, created):
    uid = created.json()["id"]
    changed = client.patch(
        base + "/Users/" + uid,
        headers={**headers, "If-Match": created.headers["etag"]},
        json={"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
              "Operations": [{"op": "replace", "path": "active", "value": False}]},
    )
    assert changed.status_code == 200, changed.text
    return uid


def test_actual_owg_routes_verify_mixed_user_lifecycle_without_identity_echo(http):
    client, db, owner, headers, base, pid = http
    active = _create(client, base, headers)
    retired = _create(client, base, headers, name="retired@acme.test")
    retired_id = _deactivate(client, base, headers, retired)
    opener = _ActualASGIRoutes(client)
    receipt = lifecycle_readback(
        "https://owg.example.test" + base, "owg.example.test", pid,
        headers["Authorization"][7:],
        _payload(pid, (active.json()["id"], True), (retired_id, False)),
        opener=opener,
    )
    assert receipt["status"] == "selected_user_states_verified"
    assert receipt["users_checked"] == 2
    assert receipt["http_gets"] == 4
    assert receipt["provider_writes_performed"] is False
    assert receipt["offboarding_device_sso_revocation"] == "not_verified"
    assert len(opener.requests) == 4
    assert all("/Users?" not in url and "/Groups" not in url for url in opener.requests)
    serialized = json.dumps(receipt)
    for secret in (pid, active.json()["id"], retired_id,
                   active.json()["userName"], headers["Authorization"][7:]):
        assert secret not in serialized


def test_actual_owg_wrong_expected_state_and_missing_user_refuse(http):
    client, db, owner, headers, base, pid = http
    created = _create(client, base, headers)
    token = headers["Authorization"][7:]
    opener = _ActualASGIRoutes(client)
    with pytest.raises(ProbeError):
        lifecycle_readback("https://owg.example.test" + base,
                           "owg.example.test", pid, token,
                           _payload(pid, (created.json()["id"], False)),
                           opener=opener)
    with pytest.raises(ProbeError):
        lifecycle_readback("https://owg.example.test" + base,
                           "owg.example.test", pid, token,
                           _payload(pid, ("unknown-opaque-id", False)),
                           opener=opener)


def test_actual_owg_revocation_during_readback_fails_final_auth_check(http):
    client, db, owner, headers, base, pid = http
    created = _create(client, base, headers)
    class Revoking(_ActualASGIRoutes):
        def open(self, req, timeout):
            result = super().open(req, timeout)
            if len(self.requests) == 2:
                revoked = client.post(
                    "/v1/admin/scim-providers/acme/" + pid + "/revoke",
                    headers=owner, json={"revision": 2},
                )
                assert revoked.status_code == 200
            return result
    opener = Revoking(client)
    with pytest.raises(ProbeError):
        lifecycle_readback(
            "https://owg.example.test" + base, "owg.example.test", pid,
            headers["Authorization"][7:],
            _payload(pid, (created.json()["id"], True)), opener=opener,
        )
    assert len(opener.requests) == 3


def test_actual_owg_router_over_real_tcp_loopback(http):
    client, db, owner, headers, base, pid = http
    created = _create(client, base, headers)
    with _tcp_scim(client) as (origin, calls):
        receipt = lifecycle_readback(
            origin + base, "127.0.0.1", pid, headers["Authorization"][7:],
            _payload(pid, (created.json()["id"], True)),
            allow_loopback_http=True,
        )
    assert receipt["users_checked"] == 1
    assert len(calls) == 3
    assert all("/Users?" not in url for url in calls)


@pytest.mark.parametrize("raw", [
    b"{}", b"not-json", b'{"format":1,"format":2}',
    b"[" + b"0," * 9000 + b"0]",
    b'{"format":"bad","provider_id":"p","users":[]}',
])
def test_invalid_manifest_refuses(raw):
    with pytest.raises(ProbeError):
        _manifest(raw, "p")


def test_duplicate_or_foreign_manifest_refuses_before_any_http(http):
    client, db, owner, headers, base, pid = http
    opener = _ActualASGIRoutes(client)
    duplicate = _payload(pid, ("one", False), ("one", True))
    foreign = _payload("different-provider", ("one", False))
    for raw in (duplicate, foreign):
        with pytest.raises(ProbeError):
            lifecycle_readback(
                "https://owg.example.test" + base, "owg.example.test", pid,
                headers["Authorization"][7:], raw, opener=opener,
            )
    assert opener.requests == []


def test_cli_requires_consent_and_emits_only_aggregate_receipt(
        http, monkeypatch, capsys):
    client, db, owner, headers, base, pid = http
    created = _create(client, base, headers)
    raw = _payload(pid, (created.json()["id"], True))
    opener = _ActualASGIRoutes(client)
    monkeypatch.setenv("OWG_SCIM_PROBE_TOKEN", headers["Authorization"][7:])
    args = ["--base-url", "https://owg.example.test" + base,
            "--confirm-host", "owg.example.test", "--confirm-provider", pid]
    assert main(args, opener=opener, stdin=io.BytesIO(raw)) == 2
    assert opener.requests == []
    assert main(args + ["--consent-user-state-read"],
                opener=opener, stdin=io.BytesIO(raw)) == 0
    receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert receipt["users_checked"] == 1
    assert created.json()["id"] not in json.dumps(receipt)
    assert pid not in json.dumps(receipt)
    assert len(opener.requests) == 3
