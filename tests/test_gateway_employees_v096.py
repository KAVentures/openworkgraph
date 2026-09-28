from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway_identity_helpers import (
    BOOT, LEGACY_ENROLL, add_admin, add_employee, bootstrap_owner, enroll, ingest, make_app, personal_invite,
)
from shared.join_code import decode_join_code


@pytest.fixture()
def gw(tmp_path):
    app, db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _totp = bootstrap_owner(client)
        yield client, db, owner


def _people(client, admin, org="acme"):
    r = client.get(f"/v1/admin/people/{org}", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def _group_link(client, admin):
    r = client.post("/v1/admin/enrollment-links", headers=admin, json={"organization_id": "acme", "organization_name": "Acme AB", "max_uses": 5})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# --- roster ---------------------------------------------------------------------------------------

def test_employee_identity_is_normalized_email_with_teams(gw):
    client, _db, owner = gw
    anna = add_employee(client, owner, " Anna.Svensson@ACME.se ", ["sales", "nordics"])
    assert anna["actor_id"] == anna["email"] == "anna.svensson@acme.se" and anna["created"] is True
    again = client.post("/v1/admin/employees/acme", headers=owner, json={"email": "anna.svensson@acme.se", "teams": ["sales"]}).json()
    assert again["created"] is False and again["employee_id"] == anna["employee_id"]
    person = _people(client, owner)["employees"][0]
    assert person["teams"] == ["sales"]
    assert client.post("/v1/admin/employees/acme", headers=owner, json={"email": "not-an-email"}).status_code == 400
    assert client.post("/v1/admin/employees/acme", headers=owner, json={"email": "b@acme.se", "teams": ["bad team!"]}).status_code == 400


def test_csv_import_reports_bad_rows_and_updates_existing(gw):
    client, _db, owner = gw
    add_employee(client, owner, "anna@acme.se")
    csv = "email,name,teams\nanna@acme.se,Anna S,sales;nordics\nerik@acme.se,Erik L,support\nnot-an-email,X,\n\n,No email,sales\n"
    r = client.post("/v1/admin/employees/acme/import", headers=owner, json={"csv": csv}).json()
    assert (r["created"], r["updated"], r["error_count"]) == (1, 1, 2)
    assert {e["line"] for e in r["errors"]} == {4, 6}
    people = {p["email"]: p for p in _people(client, owner)["employees"]}
    assert people["anna@acme.se"]["teams"] == ["nordics", "sales"] and people["anna@acme.se"]["display_name"] == "Anna S"
    headerless = client.post("/v1/admin/employees/acme/import", headers=owner, json={"csv": "lina@acme.se,Lina\n"}).json()
    assert headerless["created"] == 1


# --- personal invitations -----------------------------------------------------------------------

def test_personal_invite_locks_identity_and_join_code_round_trips(gw):
    client, db, owner = gw
    anna = add_employee(client, owner, "anna@acme.se", ["sales"])
    invite = personal_invite(client, owner, anna["employee_id"])
    decoded = decode_join_code(invite["join_code"])
    assert decoded["gateway_url"] == "https://gw.acme.test" and decoded["token"] == invite["token"]
    preview = client.get("/v1/devices/join-preview", headers={"Authorization": "Bearer " + invite["token"]}).json()
    assert preview["identity"]["locked"] is True and preview["identity"]["email"] == "anna@acme.se"
    joined = enroll(client, invite["token"], "laptop-1", actor="mallory@evil.com")
    assert joined.status_code == 200, joined.text
    assert joined.json()["actor_id"] == "anna@acme.se" and joined.json()["identity_source"] == "personal_invite"
    ingest(client, joined.json()["token"], "e1")
    with db.connect() as conn:
        actors = {row[0] for row in conn.execute("SELECT actor_id FROM evidence_events")}
    assert actors == {"anna@acme.se"}
    person = _people(client, owner)["employees"][0]
    assert [d["identity_source"] for d in person["devices"]] == ["personal_invite"]


def test_personal_invite_seats_expiry_revocation_and_duplicates(gw):
    client, db, owner = gw
    anna = add_employee(client, owner, "anna@acme.se")
    invite = personal_invite(client, owner, anna["employee_id"], max_devices=1)
    first = enroll(client, invite["token"], "laptop-1")
    assert first.status_code == 200
    assert enroll(client, invite["token"], "laptop-2").status_code == 401  # used up

    second = personal_invite(client, owner, anna["employee_id"], max_devices=2)
    dup = enroll(client, second["token"], "laptop-1")  # already enrolled: must not burn a seat
    assert dup.status_code == 409
    invites = client.get("/v1/admin/personal-invites/acme", headers=owner).json()["items"]
    assert next(i for i in invites if i["invite_id"] == second["invite_id"])["use_count"] == 0
    assert enroll(client, second["token"], "laptop-3", org="other").status_code == 403

    revoked = personal_invite(client, owner, anna["employee_id"])
    assert client.delete(f"/v1/admin/personal-invites/acme/{revoked['invite_id']}", headers=owner).status_code == 200
    assert enroll(client, revoked["token"], "laptop-4").status_code == 401
    assert client.get("/v1/devices/join-preview", headers={"Authorization": "Bearer " + revoked["token"]}).status_code == 401

    expired = personal_invite(client, owner, anna["employee_id"])
    with db.connect() as conn:
        conn.execute("UPDATE gateway_personal_invites SET expires_at = '2000-01-01T00:00:00.000000Z' WHERE invite_id = ?", (expired["invite_id"],))
    assert enroll(client, expired["token"], "laptop-5").status_code == 401


def test_invite_requiring_sso_cannot_enroll_before_confirmation(gw):
    client, db, owner = gw
    anna = add_employee(client, owner, "anna@acme.se")
    # SSO is not configured on this Gateway: requiring it is refused.
    assert client.post(f"/v1/admin/employees/acme/{anna['employee_id']}/invites", headers=owner,
                       json={"gateway_url": "https://gw.acme.test", "require_sso": True}).status_code == 400
    invite = personal_invite(client, owner, anna["employee_id"])
    with db.connect() as conn:
        conn.execute("UPDATE gateway_personal_invites SET require_sso = 1 WHERE invite_id = ?", (invite["invite_id"],))
    blocked = enroll(client, invite["token"], "laptop-1")
    assert blocked.status_code == 403 and "company sign-in" in blocked.json()["detail"]
    with db.connect() as conn:
        conn.execute("UPDATE gateway_personal_invites SET sso_verified_at = '2026-09-27T10:00:00.000000Z' WHERE invite_id = ?", (invite["invite_id"],))
    joined = enroll(client, invite["token"], "laptop-1")
    assert joined.status_code == 200 and joined.json()["identity_source"] == "sso_verified"


def test_invite_needs_https_gateway_address(gw):
    client, _db, owner = gw
    anna = add_employee(client, owner, "anna@acme.se")
    r = client.post(f"/v1/admin/employees/acme/{anna['employee_id']}/invites", headers=owner, json={"gateway_url": "http://gw.acme.test"})
    assert r.status_code == 400 and "https" in r.json()["detail"]
    assert client.get("/v1/admin/personal-invites/acme", headers=owner).json()["items"][0]["state"] == "revoked"


# --- verified identity mode, linking, offboarding -------------------------------------------------

def test_require_verified_identity_blocks_group_links_and_legacy_codes(gw):
    client, _db, owner = gw
    link = _group_link(client, owner)
    ok = enroll(client, link, "old-laptop", actor="typed@acme.se")
    assert ok.status_code == 200 and ok.json()["identity_source"] == "self_reported"
    assert client.put("/v1/admin/identity-settings/acme", headers=owner, json={"require_verified_identity": True}).status_code == 200
    assert client.get("/v1/devices/join-preview", headers={"Authorization": "Bearer " + link}).status_code == 403
    assert enroll(client, link, "new-laptop", actor="typed@acme.se").status_code == 403
    assert enroll(client, LEGACY_ENROLL, "legacy-laptop", actor="typed@acme.se").status_code == 403
    # Bound codes take the organization from the code, so leaving it out must not get around the rule.
    assert enroll(client, link, "sneaky-laptop", actor="typed@acme.se", org="").status_code == 403
    code = client.post("/v1/admin/enrollment-codes", headers=owner, json={"organization_id": "acme", "actor_id": "x@acme.se"})
    assert code.status_code == 200, code.text
    single_use = code.json()["token"]
    assert enroll(client, single_use, "sneaky-laptop-2", org="").status_code == 403
    assert enroll(client, single_use, "sneaky-laptop-2").status_code == 403
    anna = add_employee(client, owner, "anna@acme.se")
    assert enroll(client, personal_invite(client, owner, anna["employee_id"])["token"], "anna-laptop").status_code == 200
    assert client.get("/v1/admin/identity-settings/acme", headers=owner).json()["require_verified_identity"] is True


def test_link_self_reported_device_with_or_without_history(gw):
    client, db, owner = gw
    link = _group_link(client, owner)
    a = enroll(client, link, "dev-a", actor="anna-typo@acme.se").json()
    b = enroll(client, link, "dev-b", actor="erik.typed").json()
    ingest(client, a["token"], "a1")
    ingest(client, b["token"], "b1")
    anna = add_employee(client, owner, "anna@acme.se")
    erik = add_employee(client, owner, "erik@acme.se")
    unlinked = {d["device_id"] for d in _people(client, owner)["unlinked_devices"]}
    assert unlinked == {"dev-a", "dev-b"}

    moved = client.post("/v1/admin/devices/acme/dev-a/link", headers=owner, json={"employee_id": anna["employee_id"], "reattribute_history": True}).json()
    assert moved["reattributed_events"] == 1 and moved["previous_actor_ids"] == ["anna-typo@acme.se"]
    kept = client.post("/v1/admin/devices/acme/dev-b/link", headers=owner, json={"employee_id": erik["employee_id"], "reattribute_history": False}).json()
    assert kept["reattributed_events"] == 0
    ingest(client, b["token"], "b2")  # future evidence carries the linked identity
    with db.connect() as conn:
        rows = dict(conn.execute("SELECT event_id, actor_id FROM evidence_events").fetchall())
    assert rows == {"a1": "anna@acme.se", "b1": "erik.typed", "b2": "erik@acme.se"}
    people = {p["email"]: p for p in _people(client, owner)["employees"]}
    assert [d["identity_source"] for d in people["anna@acme.se"]["devices"]] == ["admin_linked"]
    assert _people(client, owner)["unlinked_devices"] == []
    audit = client.get("/v1/admin/audit/acme?limit=50", headers=owner).json()["items"]
    assert any(x["action"] == "device.linked_to_employee" and x["principal_id"] == "admin:owner@acme.se" for x in audit)


def test_offboarding_revokes_devices_and_invites(gw):
    client, _db, owner = gw
    anna = add_employee(client, owner, "anna@acme.se", ["sales"])
    joined = enroll(client, personal_invite(client, owner, anna["employee_id"])["token"], "laptop-1").json()
    pending = personal_invite(client, owner, anna["employee_id"])
    result = client.post(f"/v1/admin/employees/acme/{anna['employee_id']}/offboard", headers=owner).json()
    assert result["status"] == "offboarded" and result["revoked_devices"] == ["laptop-1"] and result["evidence_deleted"] is False
    batch = client.post("/v1/evidence/batch", headers={"Authorization": "Bearer " + joined["token"]}, json={"events": []})
    assert batch.status_code == 401
    assert enroll(client, pending["token"], "laptop-2").status_code == 401
    assert _people(client, owner)["employees"] == []
    readded = add_employee(client, owner, "anna@acme.se")
    assert readded["status"] == "active" and readded["employee_id"] == anna["employee_id"]


# --- employee self-service (/me) --------------------------------------------------------------------

def _me_session(client, device_token):
    link = client.post("/v1/devices/me-link", headers={"Authorization": "Bearer " + device_token})
    assert link.status_code == 200, link.text
    assert link.json()["path"].startswith("/me#code=")
    session = client.post("/v1/me/session", json={"code": link.json()["code"]})
    assert session.status_code == 200, session.text
    return {"Authorization": "Bearer " + session.json()["session_token"]}, link.json()["code"]


def test_me_page_shows_only_the_employees_own_data(gw):
    client, _db, owner = gw
    anna = add_employee(client, owner, "anna@acme.se", ["sales"])
    erik = add_employee(client, owner, "erik@acme.se")
    a = enroll(client, personal_invite(client, owner, anna["employee_id"])["token"], "anna-laptop").json()
    e = enroll(client, personal_invite(client, owner, erik["employee_id"])["token"], "erik-laptop").json()
    ingest(client, a["token"], "a1", "Anna's inbox")
    ingest(client, e["token"], "e1", "Erik's inbox")
    me, code = _me_session(client, a["token"])
    overview = client.get("/v1/me/overview", headers=me).json()
    assert overview["you"]["email"] == "anna@acme.se" and overview["you"]["teams"] == ["sales"]
    assert [c["device_id"] for c in overview["computers"]] == ["anna-laptop"]
    assert overview["evidence"]["total"] == 1 and overview["who_can_read"]["gateway_administrators_can_read_evidence"] is False
    evidence = client.get("/v1/me/evidence", headers=me).json()
    assert [r["window_title"] for r in evidence["rows"]] == ["Anna's inbox"]
    # The query cannot be widened: extra parameters are ignored.
    widened = client.get("/v1/me/evidence?actor_id=erik@acme.se", headers=me).json()
    assert [r["window_title"] for r in widened["rows"]] == ["Anna's inbox"]
    log = client.get("/v1/me/access-log", headers=me).json()["items"]
    assert log[0]["what"] == "You read your own evidence"
    assert client.post("/v1/me/session", json={"code": code}).status_code == 401  # single use


def test_me_link_requires_an_active_device_and_expires(gw):
    client, db, owner = gw
    service = client.post("/v1/admin/integration-tokens", headers=BOOT, json={"organization_id": "acme", "scopes": ["evidence:read"]}).json()
    assert client.post("/v1/devices/me-link", headers={"Authorization": "Bearer " + service["token"]}).status_code == 401
    assert client.post("/v1/devices/me-link").status_code == 401
    anna = add_employee(client, owner, "anna@acme.se")
    a = enroll(client, personal_invite(client, owner, anna["employee_id"])["token"], "anna-laptop").json()
    link = client.post("/v1/devices/me-link", headers={"Authorization": "Bearer " + a["token"]}).json()
    with db.connect() as conn:
        conn.execute("UPDATE gateway_me_codes SET expires_at = '2000-01-01T00:00:00.000000Z'")
    assert client.post("/v1/me/session", json={"code": link["code"]}).status_code == 401
    link = client.post("/v1/devices/me-link", headers={"Authorization": "Bearer " + a["token"]}).json()
    client.delete("/v1/admin/devices/acme/anna-laptop", headers=owner)
    assert client.post("/v1/me/session", json={"code": link["code"]}).status_code == 401
    assert client.get("/v1/me/overview", headers={"Authorization": "Bearer owg_me_session_forged"}).status_code == 401


def test_team_read_by_a_lead_appears_in_the_employees_access_log(gw):
    client, db, owner = gw
    anna = add_employee(client, owner, "anna@acme.se", ["sales"])
    a = enroll(client, personal_invite(client, owner, anna["employee_id"])["token"], "anna-laptop").json()
    db.audit(organization_id="acme", principal_id="oidc:lead", action="human_access.trace.read",
             details={"actor_id": "anna@acme.se", "access_mode": "team", "returned": 3})
    me, _code = _me_session(client, a["token"])
    log = client.get("/v1/me/access-log", headers=me).json()["items"]
    assert log[0]["what"].startswith("A team lead") and log[0]["by"] == "oidc:lead" and log[0]["rows_returned"] == 3


def test_access_log_is_readable_complete_and_only_about_the_employee(gw):
    client, db, owner = gw
    client.post("/v1/admin/employees/acme/import", headers=owner, json={"csv": "email,name\nanna_s@acme.se,Anna\nannaXs@acme.se,Other\n"})
    anna = next(p for p in _people(client, owner)["employees"] if p["email"] == "anna_s@acme.se")
    a = enroll(client, personal_invite(client, owner, anna["employee_id"])["token"], "anna-laptop").json()
    # Many unrelated audit rows must not push Anna's history out of view.
    for i in range(300):
        db.audit(organization_id="acme", principal_id="oidc:lead", action="human_access.trace.read",
                 details={"actor_id": "annaXs@acme.se", "access_mode": "team", "returned": i})
    db.audit(organization_id="acme", principal_id="tok_int", action="evidence.trace.read", details={"returned": 9, "actor_id": ""})
    db.audit(organization_id="acme", principal_id="tok_int", action="evidence.search", details={"returned": 2, "actor_id": "anna_s@acme.se"})
    db.audit(organization_id="other", principal_id="tok_x", action="evidence.trace.read", details={"returned": 1, "actor_id": ""})
    me, _code = _me_session(client, a["token"])
    log = client.get("/v1/me/access-log", headers=me).json()["items"]
    whats = [(x["what"], x["by"]) for x in log]
    assert whats == [
        ("An integration searched your evidence", "integration tok_int"),
        ("An integration read everyone's evidence, including yours", "integration tok_int"),
        ("A computer joined as you", "computer anna-laptop"),
        ("A personal invitation was created for you", "administrator owner@acme.se"),
        ("You were added to the employee roster", "administrator owner@acme.se"),
    ]


# --- pages ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,needle", [
    ("/", "Organization admin console"),
    ("/me", "What your organization holds about you"),
    ("/join/verify", "Confirm your OpenWorkGraph invitation"),
    ("/admin", "Create the first administrator"),
])
def test_pages_are_static_shells_with_strict_csp(gw, path, needle):
    client, _db, _owner = gw
    r = client.get(path)
    assert r.status_code == 200 and needle in r.text and "__NONCE__" not in r.text
    assert "script-src 'nonce-" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
