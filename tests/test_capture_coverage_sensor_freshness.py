"""Liveness-aware capture coverage for browser and desktop sensors (#634)."""
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def coverage(monkeypatch):
    from server import capture_depth, interaction_evidence, main, work_text_capture, db
    from shared import capture_control
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    monkeypatch.setattr(db, "rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(interaction_evidence, "_storage", lambda: {})
    monkeypatch.setattr(work_text_capture, "get_policy", lambda: {"capture_enabled": True})
    monkeypatch.setattr(capture_depth, "load", lambda: {
        "profile": "full", "signals": {
            "hover_dwell": True, "drag_drop": True, "pointer_trajectory": True,
            "friction_candidates": True}, "parameters": {}})
    monkeypatch.setattr(main, "BROWSER_STATUS", {})
    monkeypatch.setattr(main, "COLLECTOR_STATUS", {})
    return main, interaction_evidence


def test_connected_sensor_states_are_freshness_bounded(coverage):
    main, module = coverage
    fresh = datetime.now(timezone.utc).isoformat()
    main.BROWSER_STATUS.update({"status": "connected", "received_at": fresh})
    main.COLLECTOR_STATUS.update({"status": "recording", "received_at": fresh})
    report = module.coverage_manifest(hours=24)
    assert report["browser_sensor"]["connection"] == "connected"
    assert report["desktop_sensor"]["connection"] == "connected"
    assert report["modalities"]["browser_structural_events"] == "on"
    assert report["modalities"]["desktop_clicks_and_scrolls"] == "on"
    assert report["modalities"]["work_text"] == "on"
    assert report["modalities"]["browser_pointer_trajectory"] == "on"


def test_stale_heartbeats_do_not_claim_complete_capture(coverage):
    main, module = coverage
    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    main.BROWSER_STATUS.update({"status": "paused", "received_at": stale})
    main.COLLECTOR_STATUS.update({"status": "recording", "received_at": stale})
    report = module.coverage_manifest()
    assert report["browser_sensor"]["connection"] == "stale"
    assert report["desktop_sensor"]["connection"] == "stale"
    assert report["browser_capture_paused"] is False, "An old pause is not a current live state"
    m = report["modalities"]
    assert m["browser_structural_events"] == "browser_extension_stale"
    assert m["browser_hover_dwell"] == "on_but_browser_extension_stale"
    assert m["desktop_clicks_and_scrolls"] == "desktop_collector_stale"
    assert m["desktop_drag_drop"] == "on_but_desktop_collector_stale"
    assert m["work_text"] == "on_but_browser_extension_stale"
    assert report["missing_is_not_absence"] is True


@pytest.mark.parametrize("invalid", [
    "", "garbage", "2026-11-10T00:00:00", "not-even-a-date",
    "2025-01-01T00:00:00+00:00"
])
def test_malformed_or_missing_status_timestamp_fails_closed(coverage, invalid):
    main, module = coverage
    main.BROWSER_STATUS.update({"status": "connected", "received_at": invalid})
    report = module.coverage_manifest()
    assert report["browser_sensor"]["connection"] == "stale"
    assert report["modalities"]["browser_structural_events"] == "browser_extension_stale"


def test_no_sensor_seen_and_independent_desktop_liveness(coverage):
    main, module = coverage
    never = module.coverage_manifest()
    assert never["browser_sensor"]["connection"] == "not_seen"
    assert never["desktop_sensor"]["connection"] == "not_seen"
    assert never["modalities"]["browser_structural_events"] == "browser_extension_not_seen"
    assert never["modalities"]["desktop_clicks_and_scrolls"] == "desktop_collector_not_seen"

    main.BROWSER_STATUS.update({"status": "paused", "received_at": datetime.now(timezone.utc).isoformat()})
    paused = module.coverage_manifest()
    assert paused["browser_sensor"]["connection"] == "paused"
    assert paused["browser_capture_paused"] is True
    assert paused["modalities"]["browser_hover_dwell"] == "browser_paused"
    assert paused["modalities"]["desktop_hover_dwell"] == "on_but_desktop_collector_not_seen"


def test_disabled_signals_stay_off_even_when_sensor_stale(coverage, monkeypatch):
    main, module = coverage
    from server import capture_depth
    monkeypatch.setattr(capture_depth, "load", lambda: {
        "profile": "standard", "signals": {
            "hover_dwell": False, "drag_drop": False, "pointer_trajectory": False,
            "friction_candidates": False}, "parameters": {}})
    main.BROWSER_STATUS.update({"status": "connected", "received_at": "2023-01-01T00:00:00Z"})
    report = module.coverage_manifest()
    assert report["modalities"]["browser_structural_events"] == "browser_extension_stale"
    assert report["modalities"]["browser_hover_dwell"] == "off"
    assert report["modalities"]["browser_drag_drop"] == "off"
    assert report["modalities"]["browser_pointer_trajectory"] == "off"
    assert report["modalities"]["friction_candidates"] == "off"


@pytest.mark.parametrize("capture_state", ["paused", "stopped"])
def test_fresh_collector_does_not_overclaim_when_local_capture_stopped(coverage, monkeypatch, capture_state):
    main, module = coverage
    from shared import capture_control
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": capture_state})
    main.COLLECTOR_STATUS.update({
        "received_at": datetime.now(timezone.utc).isoformat(), "device_id": "device-a"
    })
    manifest = module.coverage_manifest()
    assert manifest["desktop_sensor"]["connection"] == capture_state
    assert manifest["desktop_sensor"]["capture_control"] == capture_state
    assert manifest["modalities"]["desktop_clicks_and_scrolls"] == "desktop_" + capture_state
    assert manifest["modalities"]["desktop_hover_dwell"] == "desktop_" + capture_state
    assert manifest["modalities"]["browser_structural_events"] == "browser_extension_not_seen"


def test_capture_control_failure_is_unknown_not_recording(coverage, monkeypatch):
    main, module = coverage
    from shared import capture_control
    monkeypatch.setattr(capture_control, "read_state", lambda: (_ for _ in ()).throw(OSError("not available")))
    main.COLLECTOR_STATUS.update({"received_at": datetime.now(timezone.utc).isoformat()})
    manifest = module.coverage_manifest()
    assert manifest["desktop_sensor"]["connection"] == "unknown"
    assert manifest["modalities"]["desktop_clicks_and_scrolls"] == "desktop_capture_state_unknown"
    assert manifest["modalities"]["desktop_hover_dwell"] == "on_but_desktop_capture_state_unknown"


def test_clock_in_future_does_not_claim_a_live_sensor(coverage):
    main, module = coverage
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    main.BROWSER_STATUS.update({"status": "connected", "received_at": future})
    main.COLLECTOR_STATUS.update({"status": "recording", "received_at": future})
    result = module.coverage_manifest()
    assert result["browser_sensor"]["connection"] == "stale"
    assert result["desktop_sensor"]["connection"] == "stale"
    assert result["modalities"]["browser_structural_events"] == "browser_extension_stale"
    assert result["modalities"]["desktop_clicks_and_scrolls"] == "desktop_collector_stale"
