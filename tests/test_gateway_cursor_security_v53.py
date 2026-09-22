from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings


def _app(tmp_path: Path):
    db = GatewayDB(f"sqlite:///{tmp_path / 'gateway.db'}")
    settings = GatewaySettings(
        database_url=db.database_url,
        admin_token="admin-secret",
        enrollment_token="enroll-secret",
        max_batch=100,
    )
    return create_app(settings=settings, db=db)


def _event(event_id: str, observed_at: str) -> dict:
    return {
        "event_id": event_id,
        "observed_at": observed_at,
        "schema_version": "1.0",
        "sensor_id": "desktop:test",
        "source": "desktop",
        "session_id": "session-test",
        "app": "Test App",
        "window_title": "Safe title",
        "event_type": "focus_span",
        "duration_seconds": 1,
        "metadata": {
            "privacy": {
                "typed_values": False,
                "clipboard_contents": False,
                "key_identities": False,
            }
        },
    }


def _decode(cursor: str) -> dict:
    padded = cursor + "=" * (-len(cursor) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def _encode(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_actor_restricted_service_cursor_cannot_be_tampered_to_broaden_scope(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        alice = client.post(
            "/v1/devices/enroll",
            headers={"Authorization": "Bearer enroll-secret"},
            json={"organization_id": "acme", "actor_id": "alice", "device_id": "alice-mac"},
        ).json()
        bob = client.post(
            "/v1/devices/enroll",
            headers={"Authorization": "Bearer enroll-secret"},
            json={"organization_id": "acme", "actor_id": "bob", "device_id": "bob-mac"},
        ).json()

        for token, event in (
            (alice["token"], _event("alice-1", "2026-09-22T09:00:00+00:00")),
            (bob["token"], _event("bob-1", "2026-09-22T09:00:30+00:00")),
            (alice["token"], _event("alice-2", "2026-09-22T09:01:00+00:00")),
        ):
            response = client.post(
                "/v1/evidence/batch",
                headers={"Authorization": f"Bearer {token}"},
                json={"events": [event]},
            )
            assert response.status_code == 200, response.text

        service = client.post(
            "/v1/admin/integration-tokens",
            headers={"Authorization": "Bearer admin-secret"},
            json={
                "organization_id": "acme",
                "actor_id": "alice",
                "scopes": ["evidence:read"],
                "label": "alice-only integration",
            },
        ).json()
        headers = {"Authorization": f"Bearer {service['token']}"}

        first = client.get("/v1/workflow-trace?limit=1", headers=headers)
        assert first.status_code == 200, first.text
        page = first.json()
        assert page["rows"][0]["actor_id"] == "alice"
        assert page["has_more"] is True

        tampered = _decode(page["next_cursor"])
        tampered["actor_id"] = "bob"
        second = client.get(
            "/v1/workflow-trace",
            params={"limit": 10, "cursor": _encode(tampered)},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        rows = second.json()["rows"]
        assert rows
        assert {row["actor_id"] for row in rows} == {"alice"}
        assert all(row["event_id"] != "bob-1" for row in rows)
