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


def _event(*, at, app, event_type="focus_span", duration=120, metadata=None, session="profile-run"):
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
        "event_type": event_type,
        "duration_seconds": duration,
        "metadata": metadata or {
            "source": "desktop",
            "activity": {
                "foreground_seconds": duration,
                "engaged_seconds": max(0, duration - 5),
                "keypress_count": 12,
                "click_count": 2,
                "scroll_count": 1,
            },
            "privacy": {"key_identities": False, "typed_values": False, "clipboard_contents": False},
        },
    }


def test_work_profile_derives_existing_evidence_without_new_sensor_data(monkeypatch):
    from server.db import init_db, insert_events
    from server.work_profile import add_self_tag, compute_work_profile

    init_db()
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", (base - timedelta(minutes=1)).isoformat())
    transfer_id = "transfer-safe-1"
    events = [
        _event(at=base, app="Salesforce", duration=120),
        _event(
            at=base + timedelta(seconds=60), app="Salesforce", event_type="clipboard_copy", duration=0,
            metadata={"source":"desktop","action":"copy","clipboard_transfer_id":transfer_id,"clipboard_contents_captured":False,"privacy":{"key_identities":False,"typed_values":False,"clipboard_contents":False}},
        ),
        _event(at=base + timedelta(seconds=120), app="ChatGPT", duration=180),
        _event(
            at=base + timedelta(seconds=200), app="ChatGPT", event_type="clipboard_paste", duration=0,
            metadata={"source":"desktop","action":"paste","clipboard_transfer_id":transfer_id,"linked_copy_event_id":events_copy_id if False else "copy-link","clipboard_contents_captured":False,"privacy":{"key_identities":False,"typed_values":False,"clipboard_contents":False}},
        ),
        _event(at=base + timedelta(seconds=300), app="Google Sheets", duration=160),
    ]
    # Link the paste to the actual copy event while preserving the same transfer id.
    events[3]["metadata"]["linked_copy_event_id"] = events[1]["event_id"]
    assert insert_events(events) == len(events)

    add_self_tag(
        category="Blocked",
        started_at=(base + timedelta(seconds=250)).isoformat(),
        ended_at=(base + timedelta(seconds=310)).isoformat(),
        session_id="profile-run",
    )
    profile = compute_work_profile(scope="current")
    assert profile["privacy"]["new_sensor_data_required"] is False
    assert profile["privacy"]["clipboard_contents_captured"] is False
    assert profile["privacy"]["individual_key_identities_captured"] is False
    assert profile["interpretation"]["not_a_productivity_score"] is True
    assert profile["fragmentation"]["surface_switches"] >= 2
    assert profile["fragmentation"]["longest_uninterrupted_surface_seconds"] >= 120
    assert profile["manual_transfer_count"] == 1
    transfer = profile["manual_transfer_patterns"][0]
    assert transfer["source_surface"] == "Salesforce"
    assert transfer["destination_surface"] == "ChatGPT"
    assert transfer["example_transfer_ids"] == [transfer_id]
    assert any(item["surface"] == "ChatGPT" and item["engaged_seconds"] > 0 for item in profile["ai_tool_usage"])
    assert profile["self_tags"][0]["category"] == "Blocked"


def test_work_profile_is_in_json_csv_and_xlsx_exports(monkeypatch):
    from server.db import init_db, insert_events
    from server.context_exporter import build_export_payload, csv_zip_bytes, json_bytes, xlsx_bytes

    init_db()
    base = datetime.now(timezone.utc) - timedelta(minutes=20)
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", (base - timedelta(minutes=1)).isoformat())
    insert_events([_event(at=base, app="ChatGPT", duration=120), _event(at=base + timedelta(seconds=120), app="Google Sheets", duration=90)])
    payload = build_export_payload(scope="current", include_raw=False)
    assert payload["work_profile"]["interpretation"]["not_a_productivity_score"] is True
    decoded = json.loads(json_bytes(payload))
    assert "work_profile" in decoded

    with zipfile.ZipFile(io.BytesIO(csv_zip_bytes(payload)), "r") as zf:
        names = set(zf.namelist())
        assert "work_profile.csv" in names
        assert "manual_transfers.csv" in names
        assert "ai_tool_usage.csv" in names
        assert "daily_rhythm.csv" in names
        assert "hunting_candidates.csv" in names
        assert "self_tags.csv" in names
        assert "WORK_PROFILE_README.md" in names

    xlsx = xlsx_bytes(payload)
    with zipfile.ZipFile(io.BytesIO(xlsx), "r") as zf:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheet_names = {sheet.attrib.get("name") for sheet in workbook.findall("m:sheets/m:sheet", ns)}
        assert {"Work profile", "Manual transfers", "AI tool usage", "Daily rhythm", "Hunting candidates", "Self tags"}.issubset(sheet_names)


def test_work_profile_routes_require_dashboard_or_api_auth(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
import server.enterprise_app
import server.evidence_delete_routes
import server.v0571_polish
import server.evidence_paging
import server.work_profile_routes
from server.secure_app import app
client=TestClient(app)
r=client.get('/v1/work-profile')
assert r.status_code == 401, r.text
r=client.get('/v1/self-tags/categories')
assert r.status_code == 401, r.text
r=client.post('/v1/self-tags',json={'category':'Blocked','minutes_back':15})
assert r.status_code == 401, r.text
'''
    env = dict(os.environ)
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "auth-data")
    result = subprocess.run([sys.executable, "-c", code], cwd=os.getcwd(), env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_secure_mcp_registers_work_profile_tool():
    source = open("mcp_server/secure_runtime.py", encoding="utf-8").read()
    assert "def get_work_profile(" in source
    assert "core._begin(name)" in source
    assert "core._finish(name, result)" in source
    assert 'secure_get("/v1/work-profile"' in source
