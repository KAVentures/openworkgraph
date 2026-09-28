from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway import employees as roster
from gateway import me_access
from gateway_identity_helpers import BOOT, add_employee, bootstrap_owner, make_app


def test_bootstrap_token_can_recover_the_only_owner(tmp_path):
    """Last-owner protection must not make bootstrap recovery impossible."""
    app, _db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _ = bootstrap_owner(client)
        admins = client.get("/v1/admin/accounts", headers=owner).json()["items"]
        assert len(admins) == 1 and admins[0]["role"] == "owner"

        reset = client.post(f"/v1/admin/accounts/{admins[0]['admin_id']}/reset", headers=BOOT)
        assert reset.status_code == 200, reset.text
        assert reset.json()["status"] == "setup_pending"
        assert reset.json()["setup_token"].startswith("owg_admin_setup_")


def test_named_owner_still_cannot_reset_the_only_owner(tmp_path):
    app, _db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _ = bootstrap_owner(client)
        admin_id = client.get("/v1/admin/accounts", headers=owner).json()["items"][0]["admin_id"]
        reset = client.post(f"/v1/admin/accounts/{admin_id}/reset", headers=owner)
        assert reset.status_code == 409


def test_sso_confirmation_is_consumed_per_device(tmp_path):
    """One successful SSO confirmation cannot enroll two computers."""
    app, db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _ = bootstrap_owner(client)
        employee = add_employee(client, owner, "anna@acme.se")
        invite = roster.create_personal_invite(
            db,
            "acme",
            employee["employee_id"],
            organization_name="Acme AB",
            max_devices=2,
            require_sso=True,
            created_by="test",
        )
        roster.mark_invite_sso_verified(
            db,
            invite["token"],
            verified_email="anna@acme.se",
            subject="sub-anna",
        )

        first = roster.enroll_with_personal_invite(
            db,
            token=invite["token"],
            requested_organization_id="acme",
            device_id="laptop-1",
        )
        assert first["identity_source"] == "sso_verified"

        with pytest.raises(roster.EmployeeError) as exc:
            roster.enroll_with_personal_invite(
                db,
                token=invite["token"],
                requested_organization_id="acme",
                device_id="laptop-2",
            )
        assert exc.value.status_code == 403

        roster.mark_invite_sso_verified(
            db,
            invite["token"],
            verified_email="anna@acme.se",
            subject="sub-anna",
        )
        second = roster.enroll_with_personal_invite(
            db,
            token=invite["token"],
            requested_organization_id="acme",
            device_id="laptop-2",
        )
        assert second["identity_source"] == "sso_verified"


def test_sso_me_refuses_to_guess_between_organizations(tmp_path):
    app, db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _ = bootstrap_owner(client)
        add_employee(client, owner, "anna@acme.se", org="acme")
        add_employee(client, owner, "anna@acme.se", org="beta")

        with pytest.raises(me_access.MeError) as exc:
            me_access.sso_session(db, email="anna@acme.se")
        assert exc.value.status_code == 409
        assert "more than one organization" in str(exc.value)

        _session, who = me_access.sso_session(db, email="anna@acme.se", organization_id="beta")
        assert who == {"organization_id": "beta", "actor_id": "anna@acme.se"}
