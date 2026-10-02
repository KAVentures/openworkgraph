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

    assert db.insert_events([allowed, blocked]) == 2
    with db.connect() as conn:
        rows = conn.execute("SELECT event_id, app, event_type, metadata_json FROM events ORDER BY id").fetchall()
    assert rows[0]["event_id"] == "evt_allowed"
    assert rows[0]["app"] == "Microsoft Excel"
    assert rows[1]["event_type"] == "discovery_scope_gap"
    assert rows[1]["app"] == ""
    encoded = json.dumps(dict(rows[1]), ensure_ascii=False)
    assert "Slack" not in encoded


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


def test_out_of_scope_focus_becomes_content_free_gap_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import prepare_recordable_event, start_session

    start_session(
        name="Scoped study",
        purpose="Keep exceptions visible without storing their content",
        allowed_apps=["Microsoft Excel"],
        duration_days=1,
    )
    now = datetime.now(timezone.utc).isoformat()
    blocked = _event(app="Microsoft Teams", observed_at=now, event_id="evt_teams")
    blocked["duration_seconds"] = 121
    prepared = prepare_recordable_event(blocked)

    assert prepared is not None
    assert prepared["event_type"] == "discovery_scope_gap"
    assert prepared["duration_seconds"] == 121
    assert prepared["app"] == ""
    assert prepared["window_title"] == ""
    encoded = json.dumps(prepared, ensure_ascii=False)
    assert "Microsoft Teams" not in encoded
    assert "Teams" not in encoded
    assert "hostname" not in encoded


def test_unresolved_browser_container_does_not_create_false_scope_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from shared.discovery_scope import prepare_recordable_event, start_session

    start_session(
        name="Scoped browser study",
        purpose="Only proven hosts",
        allowed_browser_hosts=["mail.google.com"],
        duration_days=1,
    )
    now = datetime.now(timezone.utc).isoformat()
    assert prepare_recordable_event(_event(app="Google Chrome", observed_at=now)) is None


def test_discovery_handoff_uses_readable_steps_and_omits_guessed_family_label():
    from server.procedural_feedback import _discovery_handoff_bundle

    bundle = {
        "format": "openworkgraph.workflow-evidence.v1",
        "selector": {
            "selection_mode": "explicit_executions",
            "family_key": None,
            "execution_ids": ["execution:aaaaaaaaaaaaaaaa"],
            "selected_execution_count": 1,
            "family_keys_present": ["human:email.compose_send"],
        },
        "executions": [{
            "execution_id": "execution:aaaaaaaaaaaaaaaa",
            "family_key": "human:email.compose_send",
            "family_basis": "canonical_task_family",
            "started_at": "2026-09-26T10:00:00+00:00",
            "ended_at": "2026-09-26T10:05:00+00:00",
            "steps": ["surface:gmail", "action:click", "surface:salesforce", "action:click"],
        }],
        "structural_alignment": {"high_support_minimum_runs": 1},
        "canonical_evidence_included": True,
        "canonical_evidence": [{
            "execution_id": "execution:aaaaaaaaaaaaaaaa",
            "events": [
                {
                    "event_id": "e1", "observed_at": "2026-09-26T10:00:10+00:00",
                    "event_type": "browser_click", "app": "Google Chrome", "window_title": "",
                    "metadata": {
                        "action": "click",
                        "page": {"hostname": "mail.google.com", "pathname": "/mail/u/0/", "title": ""},
                        "target": {"label": "Open email from Anna Svensson", "role": "button"},
                    },
                },
                {
                    "event_id": "e2", "observed_at": "2026-09-26T10:01:10+00:00",
                    "event_type": "browser_click", "app": "Google Chrome", "window_title": "",
                    "metadata": {
                        "action": "click",
                        "page": {"hostname": "acme.my.salesforce.com", "pathname": "/lightning/r/Account/001ABC/view", "title": ""},
                        "target": {"label": "Open account Acme AB", "role": "button"},
                    },
                },
            ],
        }],
    }

    result = _discovery_handoff_bundle(bundle)
    assert result["executions"][0]["steps"] == ["Gmail · Open email", "Salesforce · Open account"]
    encoded = json.dumps(result, ensure_ascii=False)
    assert "human:email.compose_send" not in encoded
    assert "surface:gmail" not in encoded
    assert "action:click" not in encoded


def test_scope_gap_generates_targeted_privacy_safe_question():
    from server.procedural_feedback import _suggested_questions

    bundle = {
        "executions": [
            {
                "execution_id": "execution:aaaaaaaaaaaaaaaa",
                "started_at": "2026-09-26T10:00:00+00:00",
                "ended_at": "2026-09-26T10:05:00+00:00",
                "steps": ["surface:gmail", "action:click"],
            },
            {
                "execution_id": "execution:bbbbbbbbbbbbbbbb",
                "started_at": "2026-09-26T11:00:00+00:00",
                "ended_at": "2026-09-26T11:05:00+00:00",
                "steps": ["surface:gmail", "action:click"],
            },
        ],
        "selector": {"execution_ids": ["execution:aaaaaaaaaaaaaaaa", "execution:bbbbbbbbbbbbbbbb"]},
        "structural_alignment": {"high_support_minimum_runs": 2, "less_common_observed_steps": []},
        "canonical_evidence": [
            {
                "execution_id": "execution:aaaaaaaaaaaaaaaa",
                "events": [
                    {
                        "event_id": "e1", "observed_at": "2026-09-26T10:01:00+00:00",
                        "event_type": "discovery_scope_gap", "duration_seconds": 120,
                        "app": "", "window_title": "", "metadata": {"discovery_scope_gap": True},
                    },
                    {
                        "event_id": "e2", "observed_at": "2026-09-26T10:03:01+00:00",
                        "event_type": "browser_click", "app": "Google Chrome", "window_title": "",
                        "metadata": {
                            "action": "click",
                            "page": {"hostname": "mail.google.com", "pathname": "/mail/u/0/", "title": ""},
                            "target": {"label": "Send", "role": "button"},
                        },
                    },
                ],
            },
            {"execution_id": "execution:bbbbbbbbbbbbbbbb", "events": []},
        ],
    }

    questions = _suggested_questions([bundle])
    assert questions
    assert questions[0]["reason"] == "content_free_scope_gap"
    assert "1 of 2 selected runs" in questions[0]["question"]
    assert "about 2 minutes" in questions[0]["question"]
    assert "Gmail · Send" in questions[0]["question"]
    encoded = json.dumps(questions[0], ensure_ascii=False)
    assert "Teams" not in encoded
