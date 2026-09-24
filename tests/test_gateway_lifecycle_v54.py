from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.auth import Principal, issue_token
from gateway.db import GatewayDB
from gateway.lifecycle import (
    apply_retention,
    count_evidence,
    delete_evidence,
    get_retention_policy,
    set_retention_policy,
)
from gateway.query import workflow_trace
from gateway.settings import PRODUCT_VERSION, GatewaySettings


def _db(tmp_path) -> GatewayDB:
    db = GatewayDB(f"sqlite:///{tmp_path / 'gateway.db'}")
    db.init()
    return db


def _settings(db: GatewayDB) -> GatewaySettings:
    return GatewaySettings(
        database_url=db.database_url,
        admin_token="admin-secret",
        enrollment_token="enroll-secret",
        max_batch=500,
    )


def _principal(org: str, actor: str = "alice", device: str = "device-1") -> Principal:
    return Principal(
        token_id=f"device-{org}-{actor}",
        token_type="device",
        organization_id=org,
        actor_id=actor,
        device_id=device,
        scopes=frozenset({"evidence:write", "policy:read"}),
    )


def _event(event_id: str, observed_at: datetime) -> dict:
    return {
        "event_id": event_id,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "schema_version": "1.0",
        "sensor_id": "desktop:test",
        "source": "desktop",
        "session_id": "session-1",
        "app": "Test App",
        "window_title": event_id,
        "event_type": "focus_span",
        "duration_seconds": 1,
        "metadata": {"privacy": {"typed_values": False, "clipboard_contents": False}},
    }


def test_retention_is_disabled_by_default_and_preserves_existing_trace_behavior(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    old = _event("old", now - timedelta(days=800))
    recent = _event("recent", now - timedelta(days=1))
    db.insert_events(_principal("acme"), [old, recent])

    policy = get_retention_policy(db, "acme")
    assert policy["enabled"] is False
    assert policy["retention_days"] is None

    trace = workflow_trace(db, organization_id="acme", limit=10)
    assert [row["event_id"] for row in trace["rows"]] == ["old", "recent"]


def test_retention_hides_expired_rows_before_physical_cleanup_then_apply_deletes(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    db.insert_events(
        _principal("acme"),
        [
            _event("expired", now - timedelta(days=45)),
            _event("visible", now - timedelta(days=2)),
        ],
    )
    db.audit(
        organization_id="acme",
        principal_id="test-admin",
        action="test.audit.survives",
        details={"note": "no evidence content"},
    )

    policy = set_retention_policy(db, "acme", 30)
    assert policy["enabled"] is True
    assert policy["retention_days"] == 30

    # Logical retention is immediate in the canonical trace while physical rows
    # remain until an explicit/scheduled cleanup is applied.
    trace = workflow_trace(db, organization_id="acme", limit=10)
    assert [row["event_id"] for row in trace["rows"]] == ["visible"]
    assert count_evidence(db, "acme") == 2

    preview = apply_retention(db, "acme", dry_run=True)
    assert preview["candidate_rows"] == 1
    assert preview["deleted_rows"] == 0
    assert count_evidence(db, "acme") == 2

    applied = apply_retention(db, "acme", dry_run=False)
    assert applied["candidate_rows"] == 1
    assert applied["deleted_rows"] == 1
    assert count_evidence(db, "acme") == 1
    assert any(item["action"] == "test.audit.survives" for item in db.audit_rows("acme", 20))


def test_tightening_retention_is_reapplied_to_existing_cursor(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    db.insert_events(
        _principal("acme"),
        [
            _event("twenty-days", now - timedelta(days=20)),
            _event("five-days", now - timedelta(days=5)),
            _event("one-day", now - timedelta(days=1)),
        ],
    )
    set_retention_policy(db, "acme", 30)
    first = workflow_trace(db, organization_id="acme", limit=1)
    assert first["rows"][0]["event_id"] == "twenty-days"
    assert first["next_cursor"]

    # The old cursor must not preserve the older 30-day floor after policy is
    # tightened. The server-side retention rule wins over cursor state.
    set_retention_policy(db, "acme", 10)
    second = workflow_trace(db, organization_id="acme", limit=10, cursor=first["next_cursor"])
    assert [row["event_id"] for row in second["rows"]] == ["five-days", "one-day"]


def test_actor_scoped_purge_is_tenant_scoped_and_does_not_delete_other_evidence(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    db.insert_events(_principal("acme", "alice", "alice-device"), [_event("alice-1", now)])
    db.insert_events(_principal("acme", "bob", "bob-device"), [_event("bob-1", now)])
    db.insert_events(_principal("contoso", "alice", "contoso-device"), [_event("contoso-1", now)])

    assert count_evidence(db, "acme", actor_id="alice") == 1
    deleted = delete_evidence(db, "acme", actor_id="alice")
    assert deleted == 1

    assert count_evidence(db, "acme", actor_id="alice") == 0
    assert count_evidence(db, "acme", actor_id="bob") == 1
    assert count_evidence(db, "contoso", actor_id="alice") == 1


def test_retention_can_be_disabled_without_deleting_remaining_rows(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    db.insert_events(_principal("acme"), [_event("kept", now - timedelta(days=90))])
    set_retention_policy(db, "acme", 30)
    assert workflow_trace(db, organization_id="acme", limit=10)["returned"] == 0

    disabled = set_retention_policy(db, "acme", None)
    assert disabled["enabled"] is False
    # Disabling retention restores visibility for rows that have not been
    # physically purged. Configuration changes never silently delete data.
    assert workflow_trace(db, organization_id="acme", limit=10)["rows"][0]["event_id"] == "kept"


def test_gateway_retention_admin_api_is_non_destructive_and_versioned(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    db.insert_events(_principal("acme"), [_event("old", now - timedelta(days=60))])
    app = create_app(settings=_settings(db), db=db)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == PRODUCT_VERSION

        before = count_evidence(db, "acme")
        response = client.put(
            "/v1/admin/retention/acme",
            headers={"Authorization": "Bearer admin-secret"},
            json={"retention_days": 30},
        )
        assert response.status_code == 200, response.text
        assert response.json()["retention_days"] == 30
        assert response.json()["enabled"] is True
        assert count_evidence(db, "acme") == before

        readback = client.get(
            "/v1/admin/retention/acme",
            headers={"Authorization": "Bearer admin-secret"},
        )
        assert readback.status_code == 200
        assert readback.json()["retention_days"] == 30

        disabled = client.put(
            "/v1/admin/retention/acme",
            headers={"Authorization": "Bearer admin-secret"},
            json={"retention_days": None},
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["enabled"] is False
        assert count_evidence(db, "acme") == before


def test_current_context_obeys_retention_without_deleting_rows(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    db.insert_events(
        _principal("acme"),
        [
            _event("expired-context", now - timedelta(days=90)),
            _event("recent-context", now - timedelta(days=1)),
        ],
    )
    set_retention_policy(db, "acme", 30)

    token = issue_token("owg_service")
    db.put_token(
        token_id="service-context",
        token=token,
        token_type="integration",
        organization_id="acme",
        actor_id="",
        scopes={"context:read", "evidence:read"},
    )
    app = create_app(settings=_settings(db), db=db)
    with TestClient(app) as client:
        response = client.get(
            "/v1/context/current?limit=10",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        assert [row["event_id"] for row in response.json()["rows"]] == ["recent-context"]
        # Read filtering is not a destructive side effect.
        assert count_evidence(db, "acme") == 2
