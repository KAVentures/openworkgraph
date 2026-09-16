from __future__ import annotations

import importlib
import io
import json
import zipfile


def _event(event_id: str, ts: str, event_type: str = "focus_span"):
    return {
        "event_id": event_id,
        "observed_at": ts,
        "device_id": "d1",
        "session_id": "s1",
        "app": "Google Chrome",
        "window_title": "Private subject - Gmail - Google Chrome",
        "event_type": event_type,
        "duration_seconds": 10.0 if event_type == "focus_span" else 0.0,
        "screenshot_path": None,
        "metadata": {
            "activity": {"engaged_seconds": 8, "idle_seconds": 2, "keypress_count": 22, "click_count": 2, "scroll_count": 0}
        } if event_type == "focus_span" else {
            "source": "browser_extension",
            "action": "click",
            "page": {"hostname": "mail.google.com", "pathname": "/mail/u/0/#compose/private", "title": "Private subject - Gmail"},
            "target": {"tag": "button", "label": "Send to alice@example.com"},
        },
    }


def test_session_exports_default_to_operational_layer(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", "2026-01-01T00:00:00+00:00")
    import server.db as db
    import server.exporter as exporter
    import server.analytics as analytics
    importlib.reload(db)
    importlib.reload(analytics)
    importlib.reload(exporter)
    db.init_db()
    db.insert_events([
        _event("f1", "2026-01-01T00:00:01+00:00"),
        _event("b1", "2026-01-01T00:00:05+00:00", "browser_click"),
    ])

    safe = exporter.build_export_payload(scope="current", include_raw=False)
    blob = exporter.json_bytes(safe).decode("utf-8")
    assert "operational_events" in safe
    assert "raw_local_evidence" not in safe
    assert "alice@example.com" not in blob
    assert "Private subject" not in blob
    assert "Gmail" in blob

    full = exporter.build_export_payload(scope="current", include_raw=True)
    assert "raw_local_evidence" in full
    full_blob = exporter.json_bytes(full).decode("utf-8")
    assert "alice@example.com" in full_blob

    xlsx = exporter.xlsx_bytes(safe)
    assert xlsx[:2] == b"PK"
    zipped = exporter.csv_zip_bytes(safe)
    with zipfile.ZipFile(io.BytesIO(zipped)) as zf:
        names = set(zf.namelist())
        assert "operational_events.csv" in names
        assert "inferred_tasks.csv" in names
        assert "raw_local_evidence.csv" not in names
