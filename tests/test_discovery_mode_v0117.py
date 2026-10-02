from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


def _event(*, app: str, observed_at: str, host: str = "", event_id: str = "evt_1") -> dict:
    metadata = {}
    if host:
        metadata["page"] = {"hostname": host}
    return {
        "event_id": event_id,
        "observed_at": observed_at,
        "schema_version": "1.0",
        "organization_id": "",
        "actor_id": "user",
        "device_id": "device",
        "sensor_id": "sensor",
        "source": "browser" if host else "desktop",
        "session_id": "session",
        "app": app,
        "window_title": "Example",
        "event_type": "focus_span",
        "duration_seconds": 1,
        "metadata": metadata,
    }


def test_discovery_inactive_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import event_allowed, prepare_recordable_event

    now = datetime.now(timezone.utc).isoformat()
    event = _event(app="Anything", observed_at=now)
    allowed, reason = event_allowed(event)
    assert allowed is True
    assert reason == "discovery_inactive"
    assert prepare_recordable_event(event) == event


def test_positive_scope_separates_native_apps_and_browser_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import event_allowed, start_session

    start_session(
        name="Pricing discovery",
        purpose="Map one workflow",
        allowed_apps=["Microsoft Excel"],
        allowed_browser_hosts=["mail.google.com", "*.salesforce.com"],
        duration_days=1,
    )
    now = datetime.now(timezone.utc).isoformat()

    assert event_allowed(_event(app="Microsoft Excel", observed_at=now))[0] is True
    assert event_allowed(_event(app="Slack", observed_at=now))[0] is False

    assert event_allowed(_event(app="Google Chrome", observed_at=now, host="mail.google.com"))[0] is True
    assert event_allowed(_event(app="Google Chrome", observed_at=now, host="acme.salesforce.com"))[0] is True
    assert event_allowed(_event(app="Google Chrome", observed_at=now, host="personal.example"))[0] is False

    # A generic browser app is not enough to persist an unresolved page by default.
    allowed, reason = event_allowed(_event(app="Google Chrome", observed_at=now))
    assert allowed is False
    assert reason == "unresolved_browser_context"


def test_discovery_window_blocks_post_deadline_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import event_allowed, state_path

    now = datetime.now(timezone.utc)
    state_path().write_text(json.dumps({
        "version": 1,
        "enabled": True,
        "status": "active",
        "session_id": "disc_test",
        "name": "Short study",
        "purpose": "Test",
        "starts_at": (now - timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(minutes=10)).isoformat(),
        "allowed_apps": ["Microsoft Excel"],
        "allowed_browser_hosts": [],
        "allow_unresolved_browser_container": False,
        "questions": [],
        "excluded_execution_ids": [],
    }), encoding="utf-8")

    inside = _event(app="Microsoft Excel", observed_at=now.isoformat())
    after = _event(app="Microsoft Excel", observed_at=(now + timedelta(hours=1)).isoformat(), event_id="evt_2")
    assert event_allowed(inside)[0] is True
    allowed, reason = event_allowed(after)
    assert allowed is False
    assert reason == "after_discovery_window"


def test_employee_answers_remain_separate_from_observed_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import save_question, start_session

    start_session(
        name="Pricing discovery",
        purpose="Map one workflow",
        allowed_apps=["Microsoft Excel"],
        duration_days=1,
    )
    state = save_question(
        question="Why did this run open Teams?",
        answer="Discounts above 15% need manager approval.",
        source="observed_structural_variation",
        related_execution_ids=["exec_1"],
    )
    assert state["questions"][0]["answer"].startswith("Discounts above")
    assert state["questions"][0]["source"] == "observed_structural_variation"
    assert state["questions"][0]["related_execution_ids"] == ["exec_1"]


def test_db_persistence_boundary_uses_discovery_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))

    # server.db computes DB_PATH at import time, so point it at this test directory.
    import server.db as db
    from shared.discovery_scope import start_session

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workflow_observer.db")
    db.init_db()

    start_session(
        name="Scoped study",
        purpose="Only Excel",
        allowed_apps=["Microsoft Excel"],
        duration_days=1,
    )
    now = datetime.now(timezone.utc).isoformat()
    allowed = _event(app="Microsoft Excel", observed_at=now, event_id="evt_allowed")
    blocked = _event(app="Slack", observed_at=now, event_id="evt_blocked")

    assert db.insert_events([allowed, blocked]) == 1
    with db.connect() as conn:
        rows = conn.execute("SELECT event_id, app FROM events ORDER BY event_id").fetchall()
    assert [(row["event_id"], row["app"]) for row in rows] == [("evt_allowed", "Microsoft Excel")]


def test_discovery_deactivate_restores_normal_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import deactivate_session, event_allowed, start_session

    start_session(
        name="Scoped study",
        purpose="Only Excel",
        allowed_apps=["Microsoft Excel"],
        duration_days=1,
    )
    now = datetime.now(timezone.utc).isoformat()
    assert event_allowed(_event(app="Slack", observed_at=now))[0] is False

    state = deactivate_session()
    assert state["enabled"] is False
    assert event_allowed(_event(app="Slack", observed_at=now))[0] is True


def test_review_changes_invalidate_package_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import (
        approve_share,
        finish_session,
        save_question,
        set_excluded_execution_ids,
        start_session,
    )

    start_session(
        name="Scoped study",
        purpose="Review integrity",
        allowed_apps=["Microsoft Excel"],
        duration_days=1,
    )
    finish_session()
    state = approve_share()
    assert state["share_approved_at"]

    state = save_question(question="Why did this vary?", answer="Manager approval")
    assert state["share_approved_at"] is None

    state = approve_share()
    assert state["share_approved_at"]
    state = set_excluded_execution_ids(["exec_1"])
    assert state["share_approved_at"] is None


def test_cannot_replace_unfinished_discovery_session(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import start_session

    start_session(
        name="First",
        purpose="Keep it",
        allowed_apps=["Microsoft Excel"],
        duration_days=1,
    )
    with pytest.raises(ValueError, match="existing Discovery Mode session"):
        start_session(
            name="Second",
            purpose="Should not overwrite",
            allowed_apps=["Slack"],
            duration_days=1,
        )


def test_discovery_state_can_be_read_from_explicit_endpoint_data_dir(tmp_path, monkeypatch):
    from shared.discovery_scope import read_state, state_path

    other = tmp_path / "other-default"
    endpoint = tmp_path / "endpoint-live"
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(other))
    endpoint.mkdir(parents=True, exist_ok=True)
    state_path(endpoint).write_text(json.dumps({
        "version": 1,
        "enabled": True,
        "status": "review",
        "session_id": "disc_endpoint",
        "name": "Endpoint study",
        "purpose": "Gateway guard",
        "starts_at": datetime.now(timezone.utc).isoformat(),
        "ends_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "allowed_apps": ["Microsoft Excel"],
        "allowed_browser_hosts": [],
        "gateway_event_boundary_id": 42,
        "gateway_agent_message_boundary_id": 7,
        "questions": [],
        "excluded_execution_ids": [],
    }), encoding="utf-8")

    state = read_state(data_dir=endpoint)
    assert state["session_id"] == "disc_endpoint"
    assert state["gateway_event_boundary_id"] == 42
    assert state["gateway_agent_message_boundary_id"] == 7


def test_discovery_start_persists_gateway_boundaries_in_initial_state(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import start_session

    state = start_session(
        name="Atomic boundary",
        purpose="Avoid sync race",
        allowed_apps=["Microsoft Excel"],
        duration_days=1,
        gateway_sharing_was_paused=False,
        gateway_paused_by_discovery=True,
        gateway_event_boundary_id=123,
        gateway_agent_message_boundary_id=9,
    )
    assert state["enabled"] is True
    assert state["gateway_paused_by_discovery"] is True
    assert state["gateway_event_boundary_id"] == 123
    assert state["gateway_agent_message_boundary_id"] == 9
