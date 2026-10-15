"""Organization-scoped record export must obey auth, actor, retention, deletion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.auth import Principal
from gateway.db import GatewayDB
from gateway.lifecycle import set_retention_policy
from gateway.settings import GatewaySettings

REF = "owg:r:" + "a" * 32
OTHER = "owg:r:" + "b" * 32


def _app(tmp_path):
    db = GatewayDB(f"sqlite:///{tmp_path / 'gateway.db'}")
    app = create_app(settings=GatewaySettings(
        database_url=db.database_url,
        admin_token="admin-test-secret",
        enrollment_token="enrollment-test-secret",
    ), db=db)
    return app, db


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _put(db, token, org, actor="", scopes=("evidence:read",)):
    db.put_token(
        token_id=f"id-{token}", token=token, token_type="integration",
        organization_id=org, actor_id=actor, scopes=set(scopes),
    )


def _event(n, ref=REF, when=None, private="SECRET_PATIENT_NAME"):
    return {
        "event_id": f"record-{n}",
        "observed_at": when or datetime.now(timezone.utc).isoformat(),
        "event_type": "browser_click", "source": "browser_extension",
        "session_id": f"session-{n}",
        "app": "Browser", "window_title": private,
        "metadata": {
            "resource_reference": {
                "resource_ref": ref, "provider": "jira",
                "resource_kind": "issue", "host": "team.atlassian.net",
            },
            "target": {"label": "SECRET_CUSTOMER_DETAIL"},
            "page": {"pathname": "/secret-case-012345"},
        },
    }


def _seed(db, *, org, actor, number, ref=REF, when=None):
    p = Principal(f"device-{org}-{actor}", "device", org, actor,
                  f"device-{actor}", frozenset({"evidence:write"}))
    db.insert_events(p, [_event(number, ref, when=when)])


def test_gateway_record_graph_requires_scoped_read_and_does_not_return_text(tmp_path):
    app, db = _app(tmp_path)
    with TestClient(app) as client:
        _put(db, "readalpha", "org-a")
        _put(db, "withoutscope", "org-a", scopes=("context:read",))
        _seed(db, org="org-a", actor="alice", number=1)
        _seed(db, org="org-a", actor="alice", number=2, ref=OTHER)
        _seed(db, org="org-b", actor="bob", number=3)
        url = "/v1/record-graph/export"
        params = {"resource_ref": REF}

        assert client.get(url, params=params).status_code == 401
        assert client.get(url, params=params, headers=_auth("withoutscope")).status_code == 403
        assert client.get(url, params={"resource_ref": "not-a-ref"}, headers=_auth("readalpha")).status_code == 422

        response = client.get(url, params=params, headers=_auth("readalpha"))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["observed_event_count"] == 1
        assert result["timeline"][0]["event_id"] == "record-1"
        assert result["source_evidence"]["availability"] == "unknown"
        assert result["scope"]["tenant_enforced_by_authenticated_gateway_token"] is True
        assert result["portable_export"]["source_of_truth_outcomes_not_included"] is True
        assert result["cross_device_same_business_record_joined"] is False
        for secret in ("SECRET_", "secret-case-012345", "org-a", "org-b", "team.atlassian.net"):
            assert secret not in response.text
        audits = db.audit_rows("org-a", 20)
        assert any(x["action"] == "record_graph.export.read" for x in audits)


def test_actor_scoped_token_never_reads_another_actor_or_tenant(tmp_path):
    app, db = _app(tmp_path)
    with TestClient(app) as client:
        _put(db, "onlyalice", "org-a", actor="alice")
        _put(db, "onlybob", "org-b", actor="bob")
        _seed(db, org="org-a", actor="alice", number=1)
        _seed(db, org="org-a", actor="bob", number=2)
        _seed(db, org="org-b", actor="bob", number=3)
        url = "/v1/record-graph/export"
        alpha = client.get(url, params={"resource_ref": REF}, headers=_auth("onlyalice"))
        assert alpha.status_code == 200
        assert alpha.json()["observed_event_count"] == 1
        assert alpha.json()["timeline"][0]["event_id"] == "record-1"
        forbidden = client.get(url, params={"resource_ref": REF, "actor_id": "bob"},
                               headers=_auth("onlyalice"))
        assert forbidden.status_code == 403
        beta = client.get(url, params={"resource_ref": REF}, headers=_auth("onlybob"))
        assert beta.status_code == 200
        assert beta.json()["observed_event_count"] == 1
        assert beta.json()["timeline"][0]["event_id"] == "record-3"


def test_expired_or_deleted_evidence_cannot_be_exported(tmp_path):
    app, db = _app(tmp_path)
    with TestClient(app) as client:
        _put(db, "reader", "org-a")
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        _seed(db, org="org-a", actor="alice", number=1, when=old)
        _seed(db, org="org-a", actor="alice", number=2)
        set_retention_policy(db, "org-a", 1)
        url = "/v1/record-graph/export"
        visible = client.get(url, params={"resource_ref": REF}, headers=_auth("reader"))
        assert visible.status_code == 200
        assert visible.json()["observed_event_count"] == 1
        assert visible.json()["timeline"][0]["event_id"] == "record-2"

        with db.connect() as conn:
            db._execute(conn, "DELETE FROM evidence_events WHERE organization_id = ? AND event_id = ?",
                        ("org-a", "record-2"))
        deleted = client.get(url, params={"resource_ref": REF}, headers=_auth("reader"))
        assert deleted.status_code == 200
        assert deleted.json()["record_observed"] is False
        assert deleted.json()["observed_event_count"] == 0

        assert db.revoke_token("id-reader") is True
        assert client.get(url, params={"resource_ref": REF},
                          headers=_auth("reader")).status_code == 401
