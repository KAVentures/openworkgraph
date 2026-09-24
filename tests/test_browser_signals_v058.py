from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import os
import subprocess
import sys
import uuid
import zipfile
from xml.etree import ElementTree as ET


def _event(*, at, action, hostname="example.com", pathname="/work", label="", metadata=None):
    meta = {
        "source": "browser_extension",
        "action": action,
        "page": {"hostname": hostname, "pathname": pathname, "title": "Example"},
        "target": {"role": "button", "label": label} if label else {},
        "privacy": {"typed_values": False, "clipboard_contents": False, "key_identities": False},
    }
    if metadata:
        meta.update(metadata)
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": at.isoformat(),
        "schema_version": "1.0",
        "device_id": "browser-device",
        "sensor_id": "browser:test",
        "source": "browser_extension",
        "session_id": "signal-run",
        "app": "Google Chrome",
        "window_title": "Example",
        "event_type": f"browser_{action}",
        "duration_seconds": 0,
        "metadata": meta,
    }


def test_signal_settings_defaults_and_persistence():
    from server.browser_signal_settings import load_settings, public_settings, save_settings

    assert load_settings() == {"performance_timing": True, "file_upload_category": False}
    changed = save_settings({"performance_timing": False, "file_upload_category": True})
    assert changed == {"performance_timing": False, "file_upload_category": True}
    public = public_settings()
    assert public["settings"] == changed
    never = set(public["never_captured_by_these_signals"])
    assert {"typed_text", "clipboard_contents", "file_names", "file_paths", "exact_file_sizes", "file_contents", "microphone_state", "ordinary_key_identities"}.issubset(never)


def test_work_profile_derives_friction_and_bounded_browser_metadata(monkeypatch):
    from server.browser_signal_settings import save_settings
    from server.db import init_db, insert_events
    from server.work_profile_service import compute_work_profile

    init_db()
    save_settings({"performance_timing": True, "file_upload_category": True})
    base = datetime.now(timezone.utc) - timedelta(minutes=20)
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", (base - timedelta(minutes=1)).isoformat())

    events = [
        _event(at=base, action="click", label="Retry"),
        _event(at=base + timedelta(milliseconds=350), action="click", label="Retry"),
        _event(at=base + timedelta(milliseconds=700), action="click", label="Retry"),
        _event(at=base + timedelta(seconds=5), action="page_view", pathname="/login"),
        _event(at=base + timedelta(seconds=20), action="page_view", pathname="/sso/start"),
        _event(at=base + timedelta(seconds=50), action="page_view", pathname="/dashboard"),
        _event(
            at=base + timedelta(seconds=60), action="performance_timing",
            metadata={
                "response_wait_ms": 450,
                "dom_ready_ms": 1250,
                "load_complete_ms": 1900,
                "rounded_to_ms": 50,
                "resource_urls_captured": False,
                "page_contents_captured": False,
            },
        ),
        _event(
            at=base + timedelta(seconds=70), action="file_upload_category",
            metadata={
                "file_count": 2,
                "categories": {"spreadsheet": 2},
                "filename_captured": False,
                "path_captured": False,
                "exact_size_captured": False,
                "file_contents_captured": False,
            },
        ),
    ]
    assert insert_events(events) == len(events)
    profile = compute_work_profile(scope="current")

    assert profile["rapid_click_candidates"]
    rapid = profile["rapid_click_candidates"][0]
    assert rapid["max_clicks_in_1_5s"] >= 3
    assert rapid["needs_review"] is True
    assert profile["auth_flow_candidates"]
    assert profile["auth_flow_candidates"][0]["needs_review"] is True
    assert profile["tool_waiting"][0]["median_load_ms"] == 1900.0
    assert profile["tool_waiting"][0]["interpretation"].endswith("not automatically wasted time.")
    assert profile["file_upload_categories"] == [{"surface": "Web app a379a6f6", "category": "spreadsheet", "file_count": 2}] or profile["file_upload_categories"][0]["category"] == "spreadsheet"
    assert profile["privacy"]["file_names_captured"] is False
    assert profile["privacy"]["file_paths_captured"] is False
    assert profile["privacy"]["exact_file_sizes_captured"] is False
    assert profile["privacy"]["file_contents_captured"] is False
    assert profile["privacy"]["resource_timing_urls_captured"] is False
    assert profile["privacy"]["microphone_state_captured"] is False

    from server.db import connect
    with connect() as conn:
        perf_meta = json.loads(conn.execute("SELECT metadata_json FROM context_events WHERE event_id = ?", (events[6]["event_id"],)).fetchone()[0])
        file_meta = json.loads(conn.execute("SELECT metadata_json FROM context_events WHERE event_id = ?", (events[7]["event_id"],)).fetchone()[0])
    assert perf_meta["load_complete_ms"] == 1900.0
    assert perf_meta["resource_urls_captured"] is False
    assert "resource_url" not in perf_meta
    assert file_meta["categories"] == {"spreadsheet": 2}
    assert file_meta["filename_captured"] is False
    for forbidden in ("filename", "file_path", "exact_size", "hash", "contents"):
        assert forbidden not in file_meta


def test_browser_signal_tables_are_in_csv_and_xlsx_exports(monkeypatch):
    from server.db import init_db, insert_events
    from server.context_exporter import build_export_payload, csv_zip_bytes, xlsx_bytes

    init_db()
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", (base - timedelta(seconds=5)).isoformat())
    insert_events([_event(at=base, action="performance_timing", metadata={"response_wait_ms":100,"dom_ready_ms":300,"load_complete_ms":500,"rounded_to_ms":50})])
    payload = build_export_payload(scope="current", include_raw=False)
    assert "tool_waiting" in payload["work_profile"]
    with zipfile.ZipFile(io.BytesIO(csv_zip_bytes(payload)), "r") as zf:
        names = set(zf.namelist())
        assert {"rapid_click_candidates.csv", "auth_flow_candidates.csv", "tool_waiting.csv", "file_upload_categories.csv"}.issubset(names)
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes(payload)), "r") as zf:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        names = {sheet.attrib.get("name") for sheet in workbook.findall("m:sheets/m:sheet", ns)}
        assert {"Rapid click candidates", "Auth flow candidates", "Tool waiting", "File upload categories"}.issubset(names)


def test_signal_setting_routes_are_not_public(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
import server.enterprise_app
import server.evidence_delete_routes
import server.v0571_polish
import server.evidence_paging
import server.work_profile_routes
import server.browser_signal_routes
from server.secure_app import app
client=TestClient(app)
assert client.get('/v1/browser-signal-settings').status_code == 401
assert client.post('/v1/browser-signal-settings',json={'file_upload_category':True}).status_code == 401
# browser-context remains in the paired-browser-auth route set, not a public route.
assert client.get('/v1/browser-context').status_code == 401
'''
    env = dict(os.environ)
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "auth-data")
    result = subprocess.run([sys.executable, "-c", code], cwd=os.getcwd(), env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
