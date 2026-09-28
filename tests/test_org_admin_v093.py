from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.enterprise_app import create_enterprise_app
from gateway.enrollment_links import EnrollmentLinkError, enroll_device_with_link
from gateway.hardening import HardeningSettings, PooledGatewayDB
from gateway.settings import GatewaySettings
from shared.join_code import decode_join_code, encode_join_code


ADMIN = "admin-" + "x" * 40
LEGACY_ENROLL = "enroll-" + "y" * 40
H = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture()
def setup(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'gateway.db'}"
    settings = GatewaySettings(
        database_url=db_url,
        admin_token=ADMIN,
        enrollment_token=LEGACY_ENROLL,
    )
    db = PooledGatewayDB(db_url)
    app = create_enterprise_app(settings=settings, hardening=HardeningSettings(), db=db)
    with TestClient(app) as client:
        yield client, db


def _link(client: TestClient, **overrides):
    body = {
        "organization_id": "acme",
        "organization_name": "Acme AB",
        "max_uses": 3,
        "expires_days": 14,
        "label": "Pilot",
        **overrides,
    }
    response = client.post("/v1/admin/enrollment-links", json=body, headers=H)
    assert response.status_code == 200, response.text
    return response.json()


def _join(client: TestClient, link: dict, actor: str, device: str, *, organization: str = "acme"):
    return client.post(
        "/v1/devices/enroll",
        json={"organization_id": organization, "actor_id": actor, "device_id": device},
        headers={"Authorization": "Bearer " + link["token"]},
    )


def _listed(client: TestClient) -> dict:
    response = client.get("/v1/admin/enrollment-links/acme", headers=H)
    assert response.status_code == 200
    return response.json()["items"][0]


def test_reusable_link_only_consumes_seat_after_success(setup):
    client, _db = setup
    link = _link(client)

    assert _join(client, link, "", "d0").status_code == 400
    assert _listed(client)["use_count"] == 0

    assert _join(client, link, "anna@acme.se", "d1", organization="other").status_code == 403
    assert _listed(client)["use_count"] == 0

    first = _join(client, link, "anna@acme.se", "d1")
    assert first.status_code == 200, first.text
    assert first.json()["enrollment_mode"] == "reusable_organization_link"
    assert _listed(client)["use_count"] == 1

    duplicate = _join(client, link, "anna@acme.se", "d1")
    assert duplicate.status_code == 409
    assert _listed(client)["use_count"] == 1


def test_preview_policy_and_server_owned_org_name_do_not_consume_seat(setup):
    client, _db = setup
    policy = client.put(
        "/v1/admin/policy/acme",
        json={"policy": {"share_window_titles": False, "allow_agent_events": True, "force_redacted_ai_context": True}},
        headers=H,
    )
    assert policy.status_code == 200
    link = _link(client, max_uses=1)
    auth = {"Authorization": "Bearer " + link["token"]}

    for _ in range(3):
        preview = client.get("/v1/devices/join-preview", headers=auth)
        assert preview.status_code == 200
        data = preview.json()
        assert data["organization_name"] == "Acme AB"
        assert data["seats_left"] == 1
        assert data["sharing"]["shares_window_titles"] is False
        assert data["sharing"]["shares_agent_activity"] is True
        assert data["sharing"]["forces_redacted_ai_context"] is True
        assert "typed text" in data["never_shared"]
    assert _listed(client)["use_count"] == 0


def test_expired_and_revoked_links_never_consume_seat(setup):
    client, db = setup
    expired = _link(client, label="Expired")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with db.connect() as conn:
        db._execute(conn, "UPDATE enrollment_links SET expires_at = ? WHERE grant_id = ?", (past, expired["grant_id"]))
    response = _join(client, expired, "a@acme.se", "expired-device")
    assert response.status_code == 401
    assert _listed(client)["use_count"] == 0

    active = _link(client, label="Revoked")
    revoke = client.delete(f"/v1/admin/enrollment-links/{active['grant_id']}", headers=H)
    assert revoke.status_code == 200
    response = _join(client, active, "b@acme.se", "revoked-device")
    assert response.status_code == 401
    items = client.get("/v1/admin/enrollment-links/acme", headers=H).json()["items"]
    revoked = next(item for item in items if item["grant_id"] == active["grant_id"])
    assert revoked["use_count"] == 0 and revoked["status"] == "revoked"


def test_concurrent_single_seat_link_creates_exactly_one_device(setup):
    client, db = setup
    link = _link(client, max_uses=1)

    def enroll(index: int):
        try:
            value = enroll_device_with_link(
                db,
                enrollment_token=link["token"],
                requested_organization_id="acme",
                actor_id=f"user{index}@acme.se",
                device_id=f"device-{index}",
            )
            return ("ok", value["device_id"])
        except EnrollmentLinkError as exc:
            return ("error", exc.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enroll, (1, 2)))

    assert sum(1 for status, _ in results if status == "ok") == 1, results
    assert sum(1 for status, _ in results if status == "error") == 1, results
    assert _listed(client)["use_count"] == 1
    devices = client.get("/v1/admin/devices/acme", headers=H).json()["items"]
    assert len([item for item in devices if not item["revoked_at"]]) == 1


def test_concurrent_duplicate_device_does_not_burn_second_seat(setup):
    client, db = setup
    link = _link(client, max_uses=2)

    def enroll(actor: str):
        try:
            value = enroll_device_with_link(
                db,
                enrollment_token=link["token"],
                requested_organization_id="acme",
                actor_id=actor,
                device_id="same-device",
            )
            return ("ok", value["actor_id"])
        except EnrollmentLinkError as exc:
            return ("error", exc.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enroll, ("one@acme.se", "two@acme.se")))

    assert sum(1 for status, _ in results if status == "ok") == 1, results
    assert sum(1 for status, code in results if status == "error" and code == 409) == 1, results
    assert _listed(client)["use_count"] == 1


def test_old_single_use_and_legacy_enrollment_paths_still_work(setup):
    client, _db = setup
    one_time = client.post(
        "/v1/admin/enrollment-codes",
        json={"organization_id": "acme", "actor_id": "single@acme.se", "expires_minutes": 30},
        headers=H,
    )
    assert one_time.status_code == 200
    token = one_time.json()["token"]
    enrolled = client.post(
        "/v1/devices/enroll",
        json={"organization_id": "acme", "actor_id": "single@acme.se", "device_id": "single-device"},
        headers={"Authorization": "Bearer " + token},
    )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["enrollment_mode"] == "single_use_code"

    legacy = client.post(
        "/v1/devices/enroll",
        json={"organization_id": "acme", "actor_id": "legacy@acme.se", "device_id": "legacy-device"},
        headers={"Authorization": "Bearer " + LEGACY_ENROLL},
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["enrollment_mode"] == "legacy_shared_secret"


def test_agent_sharing_policy_is_preserved_as_restrictive_ceiling(setup):
    client, _db = setup
    initial = client.get("/v1/admin/policy/acme", headers=H)
    assert initial.status_code == 200
    assert initial.json()["policy"]["allow_agent_events"] is False

    updated = client.put(
        "/v1/admin/policy/acme",
        json={"policy": {"allow_agent_events": True}},
        headers=H,
    )
    assert updated.status_code == 200
    assert client.get("/v1/admin/policy/acme", headers=H).json()["policy"]["allow_agent_events"] is True

    from connector.policy import merge_policies

    assert merge_policies({"allow_agent_events": False}, {"allow_agent_events": True})["allow_agent_events"] is False
    assert merge_policies({"allow_agent_events": True}, {"allow_agent_events": True})["allow_agent_events"] is True
    assert merge_policies({"allow_agent_events": True}, {"allow_agent_events": False})["allow_agent_events"] is False


def test_admin_console_is_static_shell_with_strict_security_headers(setup):
    client, _db = setup
    response = client.get("/admin")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'nonce-" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in csp
    assert "Organization Admin Console" in response.text
    assert "not an employee's personal dashboard" in response.text
    assert "__NONCE__" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/v1/admin/enrollment-links/acme").status_code == 401


def test_join_code_round_trip_and_url_validation():
    token = "owg_enroll_link_" + "a" * 32
    code = encode_join_code(
        gateway_url="https://owg.acme.se",
        organization_id="acme",
        token=token,
        organization_name="Acme AB",
    )
    info = decode_join_code(code)
    assert info["gateway_url"] == "https://owg.acme.se"
    assert info["organization_name"] == "Acme AB"
    assert info["token"] == token

    with pytest.raises(ValueError):
        encode_join_code(gateway_url="http://owg.acme.se", organization_id="acme", token=token)
    with pytest.raises(ValueError):
        encode_join_code(gateway_url="https://user:pass@owg.acme.se", organization_id="acme", token=token)
    with pytest.raises(ValueError):
        decode_join_code("owgjoin1.garbage")
    with pytest.raises(ValueError):
        encode_join_code(
            gateway_url="https://owg.acme.se",
            organization_id="acme",
            token="owg_device_" + "a" * 32,
        )


def test_employee_join_asset_is_explicitly_personal_and_managed_visible():
    root = Path(__file__).resolve().parents[1]
    js = (root / "dashboard" / "org_join.js").read_text(encoding="utf-8")
    routes = (root / "server" / "org_join_routes.py").read_text(encoding="utf-8")
    assert "Personal dashboard · this computer" in js
    assert "Managed by ${m.organization_name" in js
    assert "Review what will be shared" in js
    assert '<script src="/org-join.js"></script>' in routes
    assert "conflict_existing_enrollment" in routes


def test_admin_console_is_packaged():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    import tomllib
    packaged = tomllib.loads(pyproject)["tool"]["setuptools"]["package-data"]["gateway"]
    assert {"admin_console.html", "me_page.html", "join_verify.html", "landing.html"} <= set(packaged)
