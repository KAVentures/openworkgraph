from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from gateway import admin_accounts as accounts
from gateway_identity_helpers import (
    ADMIN_TOKEN, BOOT, PASSWORD, Totp, activate, add_admin, bootstrap_owner, make_app,
)


@pytest.fixture()
def gw(tmp_path):
    app, db = make_app(tmp_path)
    with TestClient(app) as client:
        yield client, db


def _sql(db, statement, params=()):
    with db.connect() as conn:
        db._execute(conn, statement, params)


# --- bootstrap and setup -------------------------------------------------------------------------

def test_bootstrap_creates_only_the_first_owner(gw):
    client, _db = gw
    state = client.get("/v1/admin/auth/state").json()
    assert state["needs_bootstrap"] is True and state["sso_enabled"] is False
    wrong = client.post("/v1/admin/auth/bootstrap", json={"bootstrap_token": "nope", "email": "a@acme.se"})
    assert wrong.status_code == 401
    owner, _totp = bootstrap_owner(client)
    assert client.get("/v1/admin/auth/state").json()["needs_bootstrap"] is False
    again = client.post("/v1/admin/auth/bootstrap", json={"bootstrap_token": ADMIN_TOKEN, "email": "x@acme.se"})
    assert again.status_code == 409
    me = client.get("/v1/admin/auth/me", headers=owner).json()
    assert me["role"] == "owner" and me["email"] == "owner@acme.se" and me["auth_method"] == "password_totp"
    # Finishing setup signs the administrator in, so it counts as their last sign-in.
    assert client.get("/v1/admin/accounts", headers=owner).json()["items"][0]["last_login_at"]


def test_setup_rejects_wrong_code_weak_password_and_link_reuse(gw):
    client, _db = gw
    created = client.post("/v1/admin/auth/bootstrap", json={"bootstrap_token": ADMIN_TOKEN, "email": "owner@acme.se"}).json()
    setup = created["setup_token"]
    secret = client.post("/v1/admin/auth/setup/start", json={"setup_token": setup}).json()["totp_secret"]
    totp = Totp(secret)
    bad_code = client.post("/v1/admin/auth/setup/complete", json={"setup_token": setup, "password": PASSWORD, "totp_code": "000000"})
    assert bad_code.status_code == 400
    weak = client.post("/v1/admin/auth/setup/complete", json={"setup_token": setup, "password": "short", "totp_code": totp.next()})
    assert weak.status_code == 400 and "12 characters" in weak.json()["detail"]
    ok = client.post("/v1/admin/auth/setup/complete", json={"setup_token": setup, "password": PASSWORD, "totp_code": totp.next()})
    assert ok.status_code == 200
    reuse = client.post("/v1/admin/auth/setup/start", json={"setup_token": setup})
    assert reuse.status_code == 401


def test_expired_setup_link_is_refused(gw):
    client, db = gw
    setup = client.post("/v1/admin/auth/bootstrap", json={"bootstrap_token": ADMIN_TOKEN, "email": "owner@acme.se"}).json()["setup_token"]
    _sql(db, "UPDATE gateway_admins SET setup_expires_at = '2000-01-01T00:00:00.000000Z'")
    assert client.post("/v1/admin/auth/setup/start", json={"setup_token": setup}).status_code == 401


# --- sign-in -----------------------------------------------------------------------------------------

def test_login_needs_password_and_fresh_authenticator_code(gw):
    client, _db = gw
    _owner, totp = bootstrap_owner(client)
    code = totp.next()
    ok = client.post("/v1/admin/auth/login", json={"email": "OWNER@acme.se", "password": PASSWORD, "totp_code": code})
    assert ok.status_code == 200
    replay = client.post("/v1/admin/auth/login", json={"email": "owner@acme.se", "password": PASSWORD, "totp_code": code})
    assert replay.status_code == 401  # a spent code cannot be used again
    wrong_pw = client.post("/v1/admin/auth/login", json={"email": "owner@acme.se", "password": "wrong password!!", "totp_code": totp.next()})
    unknown = client.post("/v1/admin/auth/login", json={"email": "ghost@acme.se", "password": PASSWORD, "totp_code": "123456"})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json()["detail"] == unknown.json()["detail"]  # no account enumeration


def test_account_locks_after_repeated_failures(gw):
    client, _db = gw
    _owner, totp = bootstrap_owner(client)
    for _ in range(accounts.LOCK_AFTER_FAILURES):
        client.post("/v1/admin/auth/login", json={"email": "owner@acme.se", "password": "wrong password!!", "totp_code": "111111"})
    locked = client.post("/v1/admin/auth/login", json={"email": "owner@acme.se", "password": PASSWORD, "totp_code": totp.next()})
    assert locked.status_code == 423


def test_source_is_throttled_across_accounts(gw):
    client, _db = gw
    bootstrap_owner(client)
    for i in range(accounts.THROTTLE_MAX_FAILURES):
        client.post("/v1/admin/auth/login", json={"email": f"nobody{i}@acme.se", "password": "x" * 12, "totp_code": "111111"})
    assert client.post("/v1/admin/auth/login", json={"email": "owner@acme.se", "password": PASSWORD, "totp_code": "111111"}).status_code == 429


def _relogin(client, db, totp):
    # Each authenticator code works once; tests signing in repeatedly within one
    # 30-second window "wait for the next code" by clearing the spent step.
    _sql(db, "UPDATE gateway_admins SET totp_last_step = 0")
    r = client.post("/v1/admin/auth/login", json={"email": "owner@acme.se", "password": PASSWORD, "totp_code": accounts.totp_now(totp.secret)})
    assert r.status_code == 200, r.text
    return r.json()


def test_sessions_expire_when_idle_or_too_old_and_on_logout(gw):
    client, db = gw
    owner, totp = bootstrap_owner(client)
    assert client.get("/v1/admin/runtime", headers=owner).status_code == 200
    _sql(db, "UPDATE gateway_admin_sessions SET last_seen_at = '2000-01-01T00:00:00.000000Z'")
    assert client.get("/v1/admin/runtime", headers=owner).status_code == 401

    fresh = _relogin(client, db, totp)
    headers = {"Authorization": "Bearer " + fresh["session_token"]}
    _sql(db, "UPDATE gateway_admin_sessions SET expires_at = '2000-01-01T00:00:00.000000Z' WHERE revoked_at IS NULL")
    assert client.get("/v1/admin/runtime", headers=headers).status_code == 401

    fresh = _relogin(client, db, totp)
    headers = {"Authorization": "Bearer " + fresh["session_token"]}
    assert client.post("/v1/admin/auth/logout", headers=headers).status_code == 200
    assert client.get("/v1/admin/runtime", headers=headers).status_code == 401


# --- roles ----------------------------------------------------------------------------------------------

def test_viewer_is_read_only_and_admin_cannot_manage_administrators(gw):
    client, _db = gw
    owner, _t = bootstrap_owner(client)
    viewer, _vt, _vid = add_admin(client, owner, "viewer@acme.se", "viewer")
    admin, _at, _aid = add_admin(client, owner, "it@acme.se", "admin")

    assert client.get("/v1/admin/policy/acme", headers=viewer).status_code == 200
    assert client.get("/v1/admin/people/acme", headers=viewer).status_code == 200
    denied = client.put("/v1/admin/policy/acme", headers=viewer, json={"policy": {}})
    assert denied.status_code == 403 and "read-only" in denied.json()["detail"]
    assert client.post("/v1/admin/employees/acme", headers=viewer, json={"email": "a@acme.se"}).status_code == 403

    assert client.put("/v1/admin/policy/acme", headers=admin, json={"policy": {"share_window_titles": False}}).status_code == 200
    assert client.get("/v1/admin/accounts", headers=admin).status_code == 403
    assert client.post("/v1/admin/accounts", headers=admin, json={"email": "x@acme.se", "role": "owner"}).status_code == 403
    assert client.get("/v1/admin/accounts", headers=owner).status_code == 200


def test_owner_safeguards(gw):
    client, _db = gw
    owner, _t = bootstrap_owner(client)
    me = client.get("/v1/admin/auth/me", headers=owner).json()
    assert client.patch(f"/v1/admin/accounts/{me['admin_id']}", headers=owner, json={"disabled": True}).status_code == 409
    assert client.patch(f"/v1/admin/accounts/{me['admin_id']}", headers=owner, json={"role": "admin"}).status_code == 409
    assert client.post(f"/v1/admin/accounts/{me['admin_id']}/reset", headers=owner).status_code == 409
    assert client.post("/v1/admin/accounts", headers=owner, json={"email": "owner@acme.se", "role": "admin"}).status_code == 409


def test_disable_ends_sessions_and_reset_issues_new_setup(gw):
    client, _db = gw
    owner, _t = bootstrap_owner(client)
    admin, totp, admin_id = add_admin(client, owner, "it@acme.se", "admin")
    assert client.patch(f"/v1/admin/accounts/{admin_id}", headers=owner, json={"disabled": True}).json()["status"] == "disabled"
    assert client.get("/v1/admin/runtime", headers=admin).status_code == 401
    assert client.post("/v1/admin/auth/login", json={"email": "it@acme.se", "password": PASSWORD, "totp_code": totp.next()}).status_code == 401
    client.patch(f"/v1/admin/accounts/{admin_id}", headers=owner, json={"disabled": False})
    reset = client.post(f"/v1/admin/accounts/{admin_id}/reset", headers=owner).json()
    assert reset["status"] == "setup_pending" and reset["setup_url"].endswith("#setup=" + reset["setup_token"])
    assert client.post("/v1/admin/auth/login", json={"email": "it@acme.se", "password": PASSWORD, "totp_code": totp.next()}).status_code == 401
    activate(client, reset["setup_token"], password="a brand new passphrase")


# --- audit and the bootstrap token -------------------------------------------------------------------

def test_audit_records_the_named_administrator(gw):
    client, _db = gw
    owner, _t = bootstrap_owner(client)
    client.put("/v1/admin/policy/acme", headers=owner, json={"policy": {"share_window_titles": False}})
    client.put("/v1/admin/policy/acme", headers=BOOT, json={"policy": {"share_window_titles": True}})
    rows = client.get("/v1/admin/audit/acme?limit=50", headers=owner).json()["items"]
    who = [r["principal_id"] for r in rows if r["action"] == "policy.updated"]
    assert set(who) == {"admin:owner@acme.se", "bootstrap-token"}


def test_bootstrap_token_can_be_disabled_for_the_api(gw, monkeypatch):
    client, _db = gw
    owner, _t = bootstrap_owner(client)
    assert client.get("/v1/admin/runtime", headers=BOOT).status_code == 200
    monkeypatch.setenv("OWG_GATEWAY_ADMIN_TOKEN_API", "disabled")
    assert client.get("/v1/admin/runtime", headers=BOOT).status_code == 401
    assert client.get("/v1/admin/auth/me", headers=BOOT).status_code == 401
    assert client.get("/v1/admin/runtime", headers=owner).status_code == 200
    assert client.get("/v1/admin/auth/state").json()["bootstrap_token_api_enabled"] is False


def test_totp_matches_rfc6238_reference_vector():
    # RFC 6238 appendix B, SHA-1, T = 59 s -> 94287082 (last 6 digits: 287082).
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32("12345678901234567890")
    assert accounts.totp_now(secret, at=59) == "287082"
    assert accounts.verify_totp(secret, "287082", at=59) == 1
    assert accounts.verify_totp(secret, "287082", last_step=1, at=59) is None


def test_passwords_are_hashed_with_scrypt():
    stored = accounts.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert accounts.verify_password(PASSWORD, stored) and not accounts.verify_password("other", stored)
    assert accounts.hash_password(PASSWORD) != stored  # salted
