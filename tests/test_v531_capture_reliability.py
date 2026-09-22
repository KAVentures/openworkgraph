from __future__ import annotations

from collector.interactions import RawInteraction
from collector.main import _capture_gap_event, _capture_health_event, _focus_span_event, _interaction_event


def _cfg() -> dict:
    return {
        "organization_id": "",
        "actor_id": "",
        "device_id": "device-test",
        "sensor_id": "desktop:test",
        "change_detection": "application",
        "upload_screenshots": False,
        "interaction_screenshots_enabled": False,
        "capture_ui_labels": False,
    }


def test_interaction_uses_context_observed_at_input_time(monkeypatch):
    # The worker must not re-attribute a queued click to whatever app happens to
    # be foreground later, after the click changed the UI.
    monkeypatch.setattr("collector.main.active_window", lambda: (_ for _ in ()).throw(AssertionError("late lookup")))
    raw = RawInteraction(
        kind="click",
        x=10,
        y=20,
        occurred_mono=123.0,
        context={"app": "Salesforce", "window_title": "Account A", "excluded": False},
    )
    event = _interaction_event(raw=raw, cfg=_cfg(), session_id="s1")
    assert event["app"] == "Salesforce"
    assert event["window_title"] == "Account A"
    assert event["metadata"]["context_observed_at_interaction"] is True


def test_periodic_focus_checkpoint_is_explicit_evidence_boundary():
    event = _focus_span_event(
        state={"app": "Excel", "window_title": "Budget", "excluded": False},
        started_at="2026-09-23T10:00:00+00:00",
        duration_seconds=120,
        cfg=_cfg(),
        session_id="s1",
        activity={"engaged_seconds": 30},
        boundary_reason="periodic_checkpoint",
    )
    assert event["event_type"] == "focus_span"
    assert event["duration_seconds"] == 120
    assert event["metadata"]["focus_boundary"] == "periodic_checkpoint"


def test_capture_gap_does_not_claim_foreground_application():
    event = _capture_gap_event(
        started_at="2026-09-23T22:00:00+00:00",
        duration_seconds=8 * 3600,
        cfg=_cfg(),
        session_id="s1",
    )
    assert event["event_type"] == "capture_gap"
    assert event["app"] == "Capture gap"
    assert event["window_title"] == ""
    assert "No foreground application is asserted" in event["metadata"]["interpretation"]


def test_capture_health_is_safe_raw_evidence_about_known_loss():
    counters = {
        "interaction_worker_errors": 1,
        "interaction_queue_dropped": 3,
        "clipboard_queue_dropped": 0,
        "capture_gap_count": 1,
        "focus_checkpoint_count": 4,
    }
    event = _capture_health_event(capture_health=counters, cfg=_cfg(), session_id="s1")
    assert event["event_type"] == "capture_health"
    assert event["metadata"]["capture_health"] == counters
    assert event["metadata"]["privacy"]["typed_values"] is False
    assert event["metadata"]["privacy"]["clipboard_contents"] is False
