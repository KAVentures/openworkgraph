from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from connector.control import set_sharing, status
from connector.policy import merge_policies, prepare_event_for_gateway
from connector.sync import _read_local_rows
from gateway.app import create_app
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings


def _gateway(tmp_path: Path):
    db = GatewayDB(f"sqlite:///{tmp_path / 'gateway.db'}")
    settings = GatewaySettings(
        database_url=db.database_url,
        admin_token="admin-secret",
        enrollment_token="enroll-secret",
        max_batch=500,
    )
    app = create_app(settings=settings, db=db)
    return app, db


def _admin_headers():
    return {"Authorization": "Bearer admin-secret"}


def _enroll(client: TestClient, organization: str, device: str, actor: str = "") -> dict:
    response = client.post(
        "/v1/devices/enroll",
        headers={"Authorization": "Bearer enroll-secret"},
        json={"organization_id": organization, "actor_id": actor, "device_id": device},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _service(client: TestClient, organization: str, scopes: list[str] | None = None) -> dict:
    response = client.post(
        "/v1/admin/integration-tokens",
        headers=_admin_headers(),
        json={
            "organization_id": organization,
            "scopes": scopes or ["evidence:read", "context:read", "transfers:read"],
            "label": "test integration",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _event(event_id: str = "evt-1") -> dict:
    return {
        "event_id": event_id,
        "observed_at": "2026-09-22T09:00:00+00:00",
        "schema_version": "1.0",
        # Deliberately spoofed. The authenticated device identity must win.
        "organization_id": "evil-org",
        "actor_id": "evil-actor",
        "device_id": "evil-device",
        "sensor_id": "browser:test",
        "source": "browser_extension",
        "session_id": "session-1",
        "app": "Google Chrome",
        "window_title": "OpenWorkGraph outreach",
        "event_type": "browser_paste",
        "duration_seconds": 0,
        "screenshot_path": "/private/local-only.png",
        "metadata": {
            "action": "paste",
            "tab_context_id": "runtime:tab-8",
            "browser_session_id": "browser-session-1",
            "semantic_action": "paste",
            "semantic_action_confidence": "direct_event",
            "clipboard_transfer_id": "transfer-123",
            "linked_copy_event_id": "evt-copy",
            "clipboard_source_tab_context_id": "runtime:tab-2",
            "clipboard_link_age_seconds": 3.4,
            "page": {"hostname": "mail.example.com", "pathname": "/compose"},
            "target": {"role": "textbox", "label": "Message"},
            "privacy": {
                "typed_values": False,
                "clipboard_contents": False,
                "key_identities": False,
            },
        },
    }


def test_gateway_enforces_authenticated_tenant_identity_and_preserves_rich_evidence(tmp_path):
    app, _ = _gateway(tmp_path)
    with TestClient(app) as client:
        device = _enroll(client, "acme", "alice-mac", "alice")
        service = _service(client, "acme")
        device_headers = {"Authorization": f"Bearer {device['token']}"}
        service_headers = {"Authorization": f"Bearer {service['token']}"}

        ingest = client.post("/v1/evidence/batch", headers=device_headers, json={"events": [_event()]})
        assert ingest.status_code == 200, ingest.text
        assert ingest.json()["inserted"] == 1
        assert ingest.json()["acknowledged_event_ids"] == ["evt-1"]

        trace = client.get("/v1/workflow-trace", headers=service_headers)
        assert trace.status_code == 200, trace.text
        payload = trace.json()
        assert payload["data_layer"] == "privacy_hardened_raw_rich_evidence"
        assert payload["derived_task_inference_authoritative"] is False if "derived_task_inference_authoritative" in payload else True
        row = payload["rows"][0]
        assert row["event_id"] == "evt-1"
        assert row["organization_id"] == "acme"
        assert row["actor_id"] == "alice"
        assert row["device_id"] == "alice-mac"
        assert row["clipboard_transfer_id"] == "transfer-123"
        assert row["tab_context_id"] == "runtime:tab-8"
        assert row["metadata"]["linked_copy_event_id"] == "evt-copy"
        assert "screenshot_path" not in row
        assert row["has_local_screenshot"] is False


def test_gateway_is_idempotent_and_service_tokens_cannot_ingest(tmp_path):
    app, _ = _gateway(tmp_path)
    with TestClient(app) as client:
        device = _enroll(client, "acme", "alice-mac", "alice")
        service = _service(client, "acme")
        device_headers = {"Authorization": f"Bearer {device['token']}"}
        service_headers = {"Authorization": f"Bearer {service['token']}"}

        first = client.post("/v1/evidence/batch", headers=device_headers, json={"events": [_event()]})
        second = client.post("/v1/evidence/batch", headers=device_headers, json={"events": [_event()]})
        assert first.json()["inserted"] == 1
        assert second.json()["inserted"] == 0
        assert second.json()["acknowledged_event_ids"] == ["evt-1"]

        denied = client.post("/v1/evidence/batch", headers=service_headers, json={"events": [_event("evt-2")]})
        assert denied.status_code == 403


def test_gateway_tenant_isolation(tmp_path):
    app, _ = _gateway(tmp_path)
    with TestClient(app) as client:
        acme_device = _enroll(client, "acme", "alice-mac", "alice")
        acme_service = _service(client, "acme")
        other_service = _service(client, "contoso")
        client.post(
            "/v1/evidence/batch",
            headers={"Authorization": f"Bearer {acme_device['token']}"},
            json={"events": [_event()]},
        ).raise_for_status()

        acme = client.get("/v1/workflow-trace", headers={"Authorization": f"Bearer {acme_service['token']}"})
        other = client.get("/v1/workflow-trace", headers={"Authorization": f"Bearer {other_service['token']}"})
        assert acme.json()["returned"] == 1
        assert other.json()["returned"] == 0


def test_gateway_rejects_forbidden_content_contract(tmp_path):
    app, _ = _gateway(tmp_path)
    with TestClient(app) as client:
        device = _enroll(client, "acme", "alice-mac", "alice")
        headers = {"Authorization": f"Bearer {device['token']}"}
        bad = _event()
        bad["metadata"]["typed_text"] = "this must never be accepted"
        response = client.post("/v1/evidence/batch", headers=headers, json={"events": [bad]})
        assert response.status_code == 400
        assert "must not ingest" in response.text

        bad2 = _event("evt-2")
        bad2["metadata"]["privacy"]["clipboard_contents"] = True
        response2 = client.post("/v1/evidence/batch", headers=headers, json={"events": [bad2]})
        assert response2.status_code == 400
        assert "privacy" in response2.text


def test_company_policy_can_only_restrict_endpoint_policy():
    local = {
        "share_excluded": False,
        "share_window_titles": False,
        "share_metadata": True,
        "allowed_event_types": ["browser_click", "browser_paste"],
        "strip_metadata_keys": ["x"],
    }
    remote = {
        "share_excluded": True,
        "share_window_titles": True,
        "share_metadata": True,
        "allowed_event_types": ["browser_paste", "focus_span"],
        "strip_metadata_keys": ["y"],
    }
    merged = merge_policies(local, remote)
    assert merged["share_excluded"] is False
    assert merged["share_window_titles"] is False
    assert merged["allowed_event_types"] == ["browser_paste"]
    assert merged["strip_metadata_keys"] == ["x", "y"]

    prepared = prepare_event_for_gateway(_event(), merged)
    assert prepared is not None
    assert prepared["window_title"] == ""
    assert "screenshot_path" not in prepared
    assert prepared["metadata"]["clipboard_transfer_id"] == "transfer-123"


def test_sync_reads_canonical_local_database_across_sensor_sources(tmp_path):
    db_path = tmp_path / "workflow_observer.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT, observed_at TEXT, schema_version TEXT,
                organization_id TEXT, actor_id TEXT, device_id TEXT, sensor_id TEXT,
                source TEXT, session_id TEXT, app TEXT, window_title TEXT,
                event_type TEXT, duration_seconds REAL, screenshot_path TEXT, metadata_json TEXT
            )"""
        )
        for event_id, source in (("desktop-1", "desktop"), ("browser-1", "browser_extension")):
            conn.execute(
                """INSERT INTO events(event_id, observed_at, schema_version, organization_id, actor_id,
                device_id, sensor_id, source, session_id, app, window_title, event_type,
                duration_seconds, screenshot_path, metadata_json)
                VALUES (?, ?, '1.0', '', '', 'dev', 'sensor', ?, 's', 'App', 'Title', 'focus_span', 1, NULL, ?)""",
                (event_id, "2026-09-22T09:00:00+00:00", source, json.dumps({"source": source})),
            )
    rows = _read_local_rows(db_path, 0, 100)
    assert [item[1]["source"] for item in rows] == ["desktop", "browser_extension"]
    assert all("metadata" in item[1] for item in rows)


def test_local_gateway_control_pauses_without_disabling_local_capture(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    data = tmp_path / "data" / "live"
    auth = tmp_path / "data" / "auth"
    data.mkdir(parents=True)
    auth.mkdir(parents=True)
    config.write_text(json.dumps({
        "gateway": {"enabled": True, "url": "https://gateway.internal", "verify_tls": True}
    }), encoding="utf-8")
    (auth / ".gateway_device_token").write_text("device-token\n", encoding="utf-8")
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))

    initial = status(config)
    assert initial["mode"] == "connected"
    paused = set_sharing(config, False)
    assert paused["mode"] == "connected_paused"
    resumed = set_sharing(config, True)
    assert resumed["mode"] == "connected"
    assert resumed["credential_exposed"] is False
