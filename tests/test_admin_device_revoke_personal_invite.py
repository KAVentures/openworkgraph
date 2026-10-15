"""IT device revoke must invalidate its issuing personal invite, not coworkers.

Test the real authenticated HTTP enrollment and revoke flow. Personal invite
with 2 seats may not be reused for a lost laptop after revocation. Already
enrolled coworkers and unrelated shared group links continue working.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from gateway_identity_helpers import (
    add_employee, bootstrap_owner, enroll, make_app, personal_invite,
)


def test_it_revoke_closes_unused_personal_invitation_seat(tmp_path):
    app, db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _totp = bootstrap_owner(client)
        worker = add_employee(client, owner, "worker@acme.se")
        invite = personal_invite(client, owner, worker["employee_id"], max_devices=2)
        joined = enroll(client, invite["token"], "missing-laptop")
        assert joined.status_code == 200, joined.text
        credential = joined.json()["token"]
        assert client.get("/v1/device-policy", headers={
            "Authorization": "Bearer " + credential,
        }).status_code == 200

        revoked = client.delete(
            "/v1/admin/devices/acme/missing-laptop", headers=owner,
        )
        assert revoked.status_code == 200, revoked.text
        data = revoked.json()
        assert data["revoked_credentials"] == 1
        assert data["personal_invitation_revoked"] is True
        assert data["evidence_deleted"] is False
        assert client.get("/v1/device-policy", headers={
            "Authorization": "Bearer " + credential,
        }).status_code == 401

        state = client.get("/v1/admin/personal-invites/acme", headers=owner).json()["items"]
        selected = next(item for item in state if item["invite_id"] == invite["invite_id"])
        assert selected["state"] == "revoked"
        assert selected["use_count"] == 1
        assert client.get("/v1/devices/join-preview", headers={
            "Authorization": "Bearer " + invite["token"],
        }).status_code == 401
        # Neither same hardware nor new spoofed device ID can spend seat 2.
        assert enroll(client, invite["token"], "missing-laptop").status_code == 401
        assert enroll(client, invite["token"], "fake-laptop-id").status_code == 401

        new = personal_invite(client, owner, worker["employee_id"], max_devices=1)
        assert enroll(client, new["token"], "replacement-approved-laptop").status_code == 200


def test_it_revoke_does_not_disconnect_second_already_enrolled_device(tmp_path):
    app, _db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _totp = bootstrap_owner(client)
        worker = add_employee(client, owner, "worker@acme.se")
        invite = personal_invite(client, owner, worker["employee_id"], max_devices=2)
        a = enroll(client, invite["token"], "lost-laptop").json()
        b = enroll(client, invite["token"], "workstation-at-office").json()
        assert client.delete("/v1/admin/devices/acme/lost-laptop", headers=owner).status_code == 200
        assert client.get("/v1/device-policy", headers={
            "Authorization": "Bearer " + a["token"],
        }).status_code == 401
        assert client.get("/v1/device-policy", headers={
            "Authorization": "Bearer " + b["token"],
        }).status_code == 200


def test_it_revoke_preserves_shared_group_invite_for_unrelated_people(tmp_path):
    app, _db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _totp = bootstrap_owner(client)
        link = client.post("/v1/admin/enrollment-links", headers=owner, json={
            "organization_id": "acme", "organization_name": "Acme AB",
            "max_uses": 5,
        })
        assert link.status_code == 200, link.text
        token = link.json()["token"]
        a = enroll(client, token, "computer-a", actor="alice@acme.se")
        assert a.status_code == 200, a.text
        revoked = client.delete("/v1/admin/devices/acme/computer-a", headers=owner)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["personal_invitation_revoked"] is False
        # A shared company code cannot be invalidated automatically without
        # breaking other employees; IT must revoke the group code separately.
        b = enroll(client, token, "computer-b", actor="bob@acme.se")
        assert b.status_code == 200, b.text


def test_it_revoke_does_not_revoke_unrelated_personal_invitations(tmp_path):
    app, _db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _totp = bootstrap_owner(client)
        a = add_employee(client, owner, "anna@acme.se")
        b = add_employee(client, owner, "bo@acme.se")
        invite_a = personal_invite(client, owner, a["employee_id"])
        invite_b = personal_invite(client, owner, b["employee_id"])
        assert enroll(client, invite_a["token"], "anna-device").status_code == 200
        assert client.delete("/v1/admin/devices/acme/anna-device", headers=owner).status_code == 200
        assert enroll(client, invite_b["token"], "bo-device").status_code == 200
