from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid


def _focus(at: datetime, app: str, seconds: int, session: str = "workday") -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": at.isoformat(),
        "schema_version": "1.0",
        "device_id": "test-device",
        "sensor_id": "test-sensor",
        "source": "desktop",
        "session_id": session,
        "app": app,
        "window_title": app,
        "event_type": "focus_span",
        "duration_seconds": seconds,
        "metadata": {
            "source": "desktop",
            "activity": {
                "foreground_seconds": seconds,
                # Deliberately zero: the profile must still preserve foreground
                # evidence rather than turning this workday into zero duration.
                "engaged_seconds": 0,
                "keypress_count": 0,
                "click_count": 1,
                "scroll_count": 0,
            },
            "privacy": {"key_identities": False, "typed_values": False, "clipboard_contents": False},
        },
    }


def _transfer(at: datetime, app: str, event_type: str, transfer_id: str, linked_copy_event_id: str = "") -> dict:
    metadata = {
        "source": "desktop",
        "action": "copy" if event_type == "clipboard_copy" else "paste",
        "clipboard_transfer_id": transfer_id,
        "clipboard_contents_captured": False,
        "privacy": {"key_identities": False, "typed_values": False, "clipboard_contents": False},
    }
    if linked_copy_event_id:
        metadata["linked_copy_event_id"] = linked_copy_event_id
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": at.isoformat(),
        "schema_version": "1.0",
        "device_id": "test-device",
        "sensor_id": "test-sensor",
        "source": "desktop",
        "session_id": "workday",
        "app": app,
        "window_title": app,
        "event_type": event_type,
        "duration_seconds": 0,
        "metadata": metadata,
    }


def test_real_today_profile_keeps_three_crm_transfers_after_restart(monkeypatch, tmp_path, request):
    """Drive the real DB/profile path for the dogfood Gmail/CRM/Sheets workday."""
    from server import analytics
    from server import db as server_db
    from server.work_profile_service import compute_work_profile

    # analytics.summary() is intentionally cached by DB revision. This test swaps
    # the process-global DB path, so isolate that cache as part of the test fixture
    # rather than letting this synthetic workday contaminate later analytics tests.
    analytics.clear_summary_cache()
    request.addfinalizer(analytics.clear_summary_cache)

    # Use monkeypatch rather than a direct module assignment so the global DB path
    # is restored after this test and cannot leak the simulated workday into later
    # analytics tests in the same pytest process.
    monkeypatch.setattr(server_db, "DB_PATH", tmp_path / "owg.db")
    server_db.init_db()
    base = datetime.now(timezone.utc) - timedelta(minutes=45)
    # Simulate a launcher restart after the observed work: current scope begins
    # after the events, while local-today history must still remain useful.
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", (base + timedelta(minutes=40)).isoformat())

    events: list[dict] = []
    for cycle in range(3):
        start = base + timedelta(minutes=cycle * 11)
        events.extend([
            _focus(start, "Gmail", 90),
            _focus(start + timedelta(seconds=90), "Salesforce", 180),
        ])
        transfer_id = f"crm-sheet-{cycle + 1}"
        copied = _transfer(start + timedelta(seconds=180), "Salesforce", "clipboard_copy", transfer_id)
        pasted = _transfer(start + timedelta(seconds=190), "Google Sheets", "clipboard_paste", transfer_id, copied["event_id"])
        events.extend([
            copied,
            _focus(start + timedelta(seconds=270), "Google Sheets", 240),
            pasted,
            _focus(start + timedelta(seconds=510), "Gmail", 150),
        ])

    assert server_db.insert_events(events) == len(events)
    current = compute_work_profile(scope="current")
    today = compute_work_profile(scope="today")

    assert float((current.get("fragmentation") or {}).get("foreground_seconds") or 0) == 0
    assert float((today.get("fragmentation") or {}).get("foreground_seconds") or 0) > 0
    assert today["manual_transfer_count"] == 3
    patterns = today["manual_transfer_patterns"]
    assert any(
        item.get("source_surface") == "Salesforce"
        and item.get("destination_surface") == "Google Sheets"
        and int(item.get("count") or 0) == 3
        for item in patterns
    )
    dump = json.dumps(today, ensure_ascii=False)
    assert "clipboard_contents" not in dump or "clipboard_contents_captured" in dump
    assert "Erik Lindqvist" not in dump and "Anna Svensson" not in dump and "SUPERSECRET" not in dump
