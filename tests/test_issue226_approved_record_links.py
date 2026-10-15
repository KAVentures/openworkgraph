"""Explicit admin mappings: cross-tenant, actor and deletion boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.auth import Principal
from gateway.db import GatewayDB
from gateway.lifecycle import set_retention_policy
from gateway.settings import GatewaySettings


REF_A = "owg:r:" + "a" * 32
REF_B = "owg:r:" + "b" * 32


def setup(tmp_path):
    db = GatewayDB(f"sqlite:///{tmp_path / 'gateway.db'}")
    app = create_app(settings=GatewaySettings(
        database_url=db.database_url, admin_token="admin-key",
        enrollment_token="enroll-key",
    ), db=db)
    return app, db


def auth(token):
    return {"Authorization": "Bearer " + token}


def put(db, org, actor, n, ref, when=None):
    p = Principal(f"device-{org}-{actor}", "device", org, actor,
                  f"device-{actor}", frozenset({"evidence:write"}))
    db.insert_events(p, [{
        "event_id": f"ev-{n}", "observed_at": when or datetime.now(timezone.utc).isoformat(),
        "event_type": "browser_click", "session_id": f"session-{n}",
        "metadata": {"resource_reference": {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": "tenant.atlassian.net",
        }, "patient": "PRIVATE_PATIENT_NAME"},
    }])


def token(db, org, name, actor=""):
    db.put_token(token_id="id-"+name, token=name, token_type="integration",
                 organization_id=org, actor_id=actor, scopes={"evidence:read"})


def test_only_admin_approves_existing_refs_and_revokes(tmp_path):
    app, db = setup(tmp_path)
    with TestClient(app) as client:
        put(db, "acme", "alice", 1, REF_A)
        put(db, "acme", "bob", 2, REF_B)
        token(db, "acme", "reader")
        token(db, "other", "outsider")
        request = {"organization_id": "acme", "first_ref": REF_A, "second_ref": REF_B}
        assert client.post("/v1/admin/record-links", json=request).status_code == 401
        assert client.post("/v1/admin/record-links", json=request, headers=auth("reader")).status_code == 401
        missing = dict(request, second_ref="owg:r:"+"c"*32)
        assert client.post("/v1/admin/record-links", json=missing,
                           headers=auth("admin-key")).status_code == 422
        approved = client.post("/v1/admin/record-links", json=request,
                               headers=auth("admin-key"))
        assert approved.status_code == 200, approved.text
        link_id = approved.json()["link_id"]
        url = "/v1/record-graph/links"
        assert client.get(url, params={"resource_ref": REF_A}).status_code == 401
        other = client.get(url, params={"resource_ref": REF_A}, headers=auth("outsider"))
        assert other.json()["links"] == []
        reader = client.get(url, params={"resource_ref": REF_A}, headers=auth("reader"))
        assert reader.status_code == 200, reader.text
        assert reader.json()["links"][0]["related_resource_ref"] == REF_B
        assert reader.json()["links"][0]["business_handoff_verified"] is False
        assert "PRIVATE" not in reader.text
        remove = client.delete(f"/v1/admin/record-links/acme/{link_id}",
                               headers=auth("admin-key"))
        assert remove.json()["revoked"] is True
        assert client.get(url, params={"resource_ref": REF_A},
                          headers=auth("reader")).json()["links"] == []


def test_actor_restriction_cannot_enumerate_other_actor_mapping(tmp_path):
    app, db = setup(tmp_path)
    with TestClient(app) as client:
        put(db, "acme", "alice", 1, REF_A)
        put(db, "acme", "bob", 2, REF_B)
        token(db, "acme", "alice-token", actor="alice")
        client.post("/v1/admin/record-links",
                    json={"organization_id": "acme", "first_ref": REF_A,
                          "second_ref": REF_B}, headers=auth("admin-key"))
        resp = client.get("/v1/record-graph/links", params={"resource_ref": REF_A},
                          headers=auth("alice-token"))
        assert resp.status_code == 200
        assert resp.json()["links"] == []
        wrong_actor = client.get("/v1/record-graph/links",
                    params={"resource_ref": REF_A, "actor_id": "bob"},
                    headers=auth("alice-token"))
        assert wrong_actor.status_code == 403


def test_expiration_of_linked_record_purges_mapping_and_does_not_resurrect(tmp_path):
    app, db = setup(tmp_path)
    with TestClient(app) as client:
        put(db, "acme", "alice", 1, REF_A)
        put(db, "acme", "alice", 2, REF_B)
        token(db, "acme", "reader")
        result = client.post("/v1/admin/record-links", headers=auth("admin-key"),
                   json={"organization_id": "acme", "first_ref": REF_A,
                         "second_ref": REF_B})
        assert result.status_code == 200, result.text
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        with db.connect() as conn:
            db._execute(conn,
                       "UPDATE evidence_events SET observed_at=? WHERE organization_id=? AND event_id=?",
                       (old, "acme", "ev-2"))
        set_retention_policy(db, "acme", 1)
        read = client.get("/v1/record-graph/links", headers=auth("reader"),
                          params={"resource_ref": REF_A})
        assert read.json()["links"] == []
        with db.connect() as conn:
            remaining = db._execute(conn, "SELECT COUNT(*) FROM approved_record_links").fetchone()[0]
        assert remaining == 0


def test_physical_deletion_immediately_prunes_record_link_index(tmp_path):
    from gateway.lifecycle import delete_evidence
    app, db = setup(tmp_path)
    with TestClient(app) as client:
        put(db, "acme", "alice", 1, REF_A)
        put(db, "acme", "alice", 2, REF_B)
        token(db, "acme", "reader")
        approved = client.post("/v1/admin/record-links", headers=auth("admin-key"),
               json={"organization_id": "acme", "first_ref": REF_A, "second_ref": REF_B})
        assert approved.status_code == 200
        deleted = delete_evidence(db, "acme", session_id="session-2")
        assert deleted == 1
        with db.connect() as conn:
            row = db._execute(conn,
                "SELECT COUNT(*) FROM approved_record_links WHERE organization_id=?",
                ("acme",)).fetchone()
            assert row[0] == 0
