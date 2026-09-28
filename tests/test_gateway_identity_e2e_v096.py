from __future__ import annotations

"""End to end: a real Gateway process and the employee's real local join code.

Admin creates an employee and a personal invitation over HTTP; the local
OpenWorkGraph join flow (the same functions the dashboard calls) previews and
joins with it; the computer syncs evidence; the local "what does my organization
hold about me" link opens the employee's /me session on the Gateway.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from gateway.admin_accounts import totp_now

ROOT = Path(__file__).resolve().parents[1]
ADMIN_TOKEN = "admin-" + "e" * 40


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def gateway(tmp_path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({
        "OWG_GATEWAY_DATABASE_URL": f"sqlite:///{tmp_path / 'gateway.db'}",
        "OWG_GATEWAY_ADMIN_TOKEN": ADMIN_TOKEN,
        "PYTHONPATH": str(ROOT),
    })
    for var in ("OWG_GATEWAY_SSO_ISSUER", "OWG_GATEWAY_OIDC_ISSUER", "OWG_GATEWAY_SSO_CLIENT_ID", "OWG_GATEWAY_PUBLIC_URL"):
        env.pop(var, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "gateway.human_enterprise_app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(base + "/health", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        proc.terminate()
        raise AssertionError("Gateway did not start: " + (proc.stderr.read() if proc.stderr else ""))
    try:
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture()
def local(tmp_path):
    """The employee's OpenWorkGraph, with its config and data in a temp folder.

    Each step runs the real local join code in its own process (see local_join_child.py).
    """
    home = tmp_path / "local"
    home.mkdir()
    config = home / "config.json"
    config.write_text(json.dumps({"device_id": "anna-macbook"}), encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(home / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(home / "auth"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "PYTHONPATH": str(ROOT),
    })

    def run(action: str, *args: str) -> dict:
        done = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "local_join_child.py"), str(config), action, *args],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    return run, config


def _admin_session(base: str) -> dict[str, str]:
    created = httpx.post(base + "/v1/admin/auth/bootstrap", json={"bootstrap_token": ADMIN_TOKEN, "email": "owner@acme.se"}).json()
    started = httpx.post(base + "/v1/admin/auth/setup/start", json={"setup_token": created["setup_token"]}).json()
    done = httpx.post(base + "/v1/admin/auth/setup/complete", json={
        "setup_token": created["setup_token"], "password": "correct horse battery", "totp_code": totp_now(started["totp_secret"]),
    }).json()
    return {"Authorization": "Bearer " + done["session_token"]}


def test_personal_invite_joins_real_local_app_and_opens_me(gateway, local):
    run, config = local
    admin = _admin_session(gateway)
    anna = httpx.post(gateway + "/v1/admin/employees/acme", headers=admin, json={"email": "anna@acme.se", "display_name": "Anna Svensson", "teams": ["sales"]}).json()
    invite = httpx.post(gateway + f"/v1/admin/employees/acme/{anna['employee_id']}/invites", headers=admin,
                        json={"organization_name": "Acme AB", "gateway_url": gateway}).json()
    code = invite["join_code"]

    preview = run("preview", code)
    assert preview["organization_name"] == "Acme AB"
    assert preview["identity"]["locked"] is True and preview["identity"]["email"] == "anna@acme.se"

    # Whatever the employee might type, the computer joins as the invited person.
    joined = run("join", code, "someone-else@evil.com")
    assert joined["actor_id"] == "anna@acme.se"
    saved = json.loads(config.read_text(encoding="utf-8"))
    # Identity lives on the Gateway credential; the computer only stores the connection.
    assert saved["gateway"]["enabled"] is True and saved["gateway"]["url"] == gateway

    # The computer's credential works for syncing...
    device = {"Authorization": "Bearer " + run("device-token")["token"]}
    batch = httpx.post(gateway + "/v1/evidence/batch", headers=device, json={"events": [{
        "event_id": "e1", "observed_at": "2026-09-27T10:00:00Z", "event_type": "focus_span", "app": "Outlook", "window_title": "Inbox",
    }]})
    assert batch.status_code == 200

    # ...and the admin sees a verified, personal-invite computer.
    people = httpx.get(gateway + "/v1/admin/people/acme", headers=admin).json()
    assert people["unlinked_devices"] == []
    devices = people["employees"][0]["devices"]
    assert [d["identity_source"] for d in devices] == ["personal_invite"]
    assert devices[0]["device_id"] == joined["device_id"] and devices[0]["events"] == 1

    # "See what your organization holds about you" from the local dashboard.
    link = run("me-link")
    assert link["url"].startswith(gateway + "/me#code=owg_me_code_")
    session = httpx.post(gateway + "/v1/me/session", json={"code": link["url"].split("#code=", 1)[1]}).json()
    me = {"Authorization": "Bearer " + session["session_token"]}
    overview = httpx.get(gateway + "/v1/me/overview", headers=me).json()
    assert overview["you"]["email"] == "anna@acme.se" and overview["evidence"]["total"] == 1
    assert [r["window_title"] for r in httpx.get(gateway + "/v1/me/evidence", headers=me).json()["rows"]] == ["Inbox"]

    # The group-invite path is still identity-typed and shows up as unverified.
    link_token = httpx.post(gateway + "/v1/admin/enrollment-links", headers=admin, json={"organization_id": "acme", "organization_name": "Acme AB"}).json()["token"]
    enrolled = httpx.post(gateway + "/v1/devices/enroll", headers={"Authorization": "Bearer " + link_token},
                          json={"organization_id": "acme", "actor_id": "erik.typed", "device_id": "erik-pc"}).json()
    assert enrolled["identity_source"] == "self_reported"
    assert [d["device_id"] for d in httpx.get(gateway + "/v1/admin/people/acme", headers=admin).json()["unlinked_devices"]] == ["erik-pc"]


def test_local_join_refuses_unconfirmed_sso_invite_with_clear_message(gateway, local, tmp_path):
    run, _config = local
    admin = _admin_session(gateway)
    anna = httpx.post(gateway + "/v1/admin/employees/acme", headers=admin, json={"email": "anna@acme.se"}).json()
    invite = httpx.post(gateway + f"/v1/admin/employees/acme/{anna['employee_id']}/invites", headers=admin,
                        json={"organization_name": "Acme AB", "gateway_url": gateway}).json()
    import sqlite3
    db = sqlite3.connect(tmp_path / "gateway.db")
    db.execute("UPDATE gateway_personal_invites SET require_sso = 1")
    db.commit()
    preview = run("preview", invite["join_code"])
    assert preview["identity"]["require_sso"] is True
    assert preview["identity"]["verify_url"].startswith(gateway + "/join/verify#code=owgjoin1.")
    refused = run("join", invite["join_code"])
    assert refused["status"] == 400 and "Confirm it's you" in refused["detail"]
