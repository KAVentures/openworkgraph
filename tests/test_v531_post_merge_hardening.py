from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from connector.control import set_sharing
from connector.policy import merge_policies, prepare_event_for_gateway
from connector.state import SyncState
from connector.sync import _push_batch_resilient
from gateway.app import create_app
from gateway.auth import issue_token
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings
from server.exporter import _spreadsheet_safe_value, xlsx_bytes
from shared.time_utils import normalize_timestamp


def _settings(tmp_path: Path) -> tuple[GatewaySettings, GatewayDB]:
    db = GatewayDB(f"sqlite:///{tmp_path / 'gateway.db'}")
    settings = GatewaySettings(
        database_url=db.database_url,
        admin_token="admin-secret",
        enrollment_token="enroll-secret",
        max_batch=500,
    )
    return settings, db


def _event(event_id: str, observed_at: str, event_type: str = "focus_span", transfer: str = "") -> dict:
    metadata = {"privacy": {"typed_values": False, "clipboard_contents": False, "key_identities": False}}
    if transfer:
        metadata["clipboard_transfer_id"] = transfer
    return {
        "event_id": event_id,
        "observed_at": observed_at,
        "event_type": event_type,
        "app": "Test",
        "window_title": "Title",
        "duration_seconds": 0,
        "metadata": metadata,
    }


def test_disjoint_policy_allowlists_fail_closed():
    merged = merge_policies(
        {"allowed_event_types": ["focus_span"]},
        {"allowed_event_types": ["browser_click"]},
    )
    assert merged["allowed_event_types"] == []
    assert merged["_deny_all_event_types"] is True
    assert prepare_event_for_gateway(_event("x", "2026-09-22T10:00:00Z"), merged) is None


def test_timestamp_offsets_normalize_to_same_instant_and_invalid_is_rejected():
    assert normalize_timestamp("2026-09-22T12:00:00+02:00") == "2026-09-22T10:00:00.000000Z"
    assert normalize_timestamp("2026-09-22T10:00:00Z") == "2026-09-22T10:00:00.000000Z"
    try:
        normalize_timestamp("not-a-date")
        raise AssertionError("invalid timestamp accepted")
    except ValueError:
        pass


def test_bad_gateway_cursor_is_400_and_offset_query_is_chronological(tmp_path):
    settings, db = _settings(tmp_path)
    app = create_app(settings=settings, db=db)
    with TestClient(app) as client:
        device_token = issue_token("owg_device")
        service_token = issue_token("owg_service")
        db.put_token(
            token_id="device-test", token=device_token, token_type="device",
            organization_id="acme", actor_id="alice", device_id="d1",
            scopes={"evidence:write", "policy:read"},
        )
        db.put_token(
            token_id="service-test", token=service_token, token_type="integration",
            organization_id="acme", actor_id="", device_id="",
            scopes={"evidence:read", "context:read", "transfers:read"},
        )
        ingest = client.post(
            "/v1/evidence/batch",
            headers={"Authorization": f"Bearer {device_token}"},
            json={"events": [_event("e1", "2026-09-22T12:00:00+02:00")]},
        )
        assert ingest.status_code == 200, ingest.text
        trace = client.get(
            "/v1/workflow-trace?since=2026-09-22T11%3A59%3A59%2B02%3A00",
            headers={"Authorization": f"Bearer {service_token}"},
        )
        assert trace.status_code == 200, trace.text
        assert trace.json()["returned"] == 1
        assert trace.json()["rows"][0]["observed_at"] == "2026-09-22T10:00:00.000000Z"
        bad = client.get(
            "/v1/workflow-trace?cursor=definitely-not-a-cursor",
            headers={"Authorization": f"Bearer {service_token}"},
        )
        assert bad.status_code == 400


def test_transfers_filter_before_limit_so_noise_does_not_hide_transfer(tmp_path):
    settings, db = _settings(tmp_path)
    app = create_app(settings=settings, db=db)
    with TestClient(app) as client:
        device_token = issue_token("owg_device")
        service_token = issue_token("owg_service")
        db.put_token(
            token_id="device-transfer", token=device_token, token_type="device",
            organization_id="acme", actor_id="alice", device_id="d1",
            scopes={"evidence:write", "policy:read"},
        )
        db.put_token(
            token_id="service-transfer", token=service_token, token_type="integration",
            organization_id="acme", actor_id="", device_id="",
            scopes={"evidence:read", "transfers:read"},
        )
        events = [
            _event(f"noise-{i}", f"2026-09-22T10:{i // 60:02d}:{i % 60:02d}Z")
            for i in range(400)
        ]
        events.append(_event("paste", "2026-09-22T10:07:00Z", "browser_paste", "transfer-late"))
        for start in range(0, len(events), 100):
            response = client.post(
                "/v1/evidence/batch",
                headers={"Authorization": f"Bearer {device_token}"},
                json={"events": events[start:start + 100]},
            )
            assert response.status_code == 200, response.text
        transfers = client.get(
            "/v1/transfers?limit=10",
            headers={"Authorization": f"Bearer {service_token}"},
        )
        assert transfers.status_code == 200, transfers.text
        assert [x["clipboard_transfer_id"] for x in transfers.json()["items"]] == ["transfer-late"]


def test_terminal_bad_event_isolated_instead_of_blocking_batch():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        events = payload["events"]
        if any(e["event_id"] == "bad" for e in events):
            return httpx.Response(400, json={"detail": "bad event"}, request=request)
        return httpx.Response(
            200,
            json={"acknowledged_event_ids": [e["event_id"] for e in events]},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        acknowledged, rejected = _push_batch_resilient(
            client,
            "https://gateway.invalid",
            [
                _event("good-1", "2026-09-22T10:00:00Z"),
                _event("bad", "2026-09-22T10:00:01Z"),
                _event("good-2", "2026-09-22T10:00:02Z"),
            ],
        )
    assert acknowledged == {"good-1", "good-2"}
    assert set(rejected) == {"bad"}


def test_pause_creates_permanent_local_only_interval(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    data = tmp_path / "data" / "live"
    auth = tmp_path / "data" / "auth"
    data.mkdir(parents=True)
    auth.mkdir(parents=True)
    config.write_text(json.dumps({"gateway": {"enabled": True, "url": "https://gateway.internal"}}))
    (auth / ".gateway_device_token").write_text("device-token\n")
    db_path = data / "workflow_observer.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT)")
        conn.execute("INSERT INTO events(event_id) VALUES ('before')")
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))

    set_sharing(config, False)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO events(event_id) VALUES ('paused-1')")
        conn.execute("INSERT INTO events(event_id) VALUES ('paused-2')")
    set_sharing(config, True)

    state = SyncState(data / "gateway_sync_state.db")
    assert state.skipped(1) is False
    assert state.skipped(2) is True
    assert state.skipped(3) is True


def test_spreadsheet_formula_strings_are_neutralized_without_mutating_json_semantics():
    assert _spreadsheet_safe_value("=HYPERLINK(\"https://evil.invalid\")") .startswith("'")
    assert _spreadsheet_safe_value("normal title") == "normal title"

    payload = {
        "export": {
            "version": "test", "generated_at": "now", "scope": "all",
            "run_started_at": None, "include_raw_local_evidence": True,
            "evidence_tables_truncated": False, "privacy_note": "test",
            "interpretation_note": "test",
        },
        "overview": {"operational": {}},
        "capture_manifest": {},
        "operational_events": [{"window_title": "=1+1"}],
        "raw_local_evidence": [],
    }
    data = xlsx_bytes(payload)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        worksheet_xml = "".join(
            zf.read(name).decode("utf-8", errors="ignore")
            for name in zf.namelist() if name.startswith("xl/worksheets/")
        )
        assert "<f>1+1</f>" not in worksheet_xml
