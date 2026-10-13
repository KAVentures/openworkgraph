"""Authenticated, content-free recorder liveness: no inference from event age."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.db import GatewayDB
from gateway.public_plugin_mcp import _reported_recorder_state
from gateway.settings import GatewaySettings


def _setup(tmp_path):
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    settings = GatewaySettings(
        database_url=db.database_url,
        admin_token="admin-token", enrollment_token="enroll-token",
    )
    return create_app(settings=settings, db=db), db


def _enroll(client, *, org, actor, device):
    res = client.post("/v1/devices/enroll",
                      headers={"Authorization": "Bearer enroll-token"},
                      json={"organization_id": org, "actor_id": actor,
                            "device_id": device})
    assert res.status_code == 200, res.text
    return {"Authorization": "Bearer " + res.json()["token"]}


def test_hosted_recorder_state_requires_recent_device_report(tmp_path):
    app, db = _setup(tmp_path)
    with TestClient(app) as client:
        a = _enroll(client, org="tenant", actor="alice", device="alice-mac")
        b = _enroll(client, org="tenant", actor="bob", device="bob-mac")
        absent = _reported_recorder_state(db, organization_id="tenant", actor_id="alice")
        assert absent["local_recorder_state"] == "unknown_to_hosted_connector"
        assert absent["desktop_collector_heartbeat_verified"] is False
        resp = client.post("/v1/device/recorder-heartbeat", headers=a, json={
            "collector_state": "recording",
            "local_capture_policy": "recording",
            "collector_heartbeat_age_seconds": 3,
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["contents_received"] is False
        report = _reported_recorder_state(db, organization_id="tenant", actor_id="alice")
        assert report["local_recorder_state"] == "recording"
        assert report["fresh_authenticated_device_report"] is True
        assert report["desktop_collector_heartbeat_verified"] is True
        assert _reported_recorder_state(
            db, organization_id="tenant", actor_id="bob",
        )["local_recorder_state"] == "unknown_to_hosted_connector"
        invalid = client.post("/v1/device/recorder-heartbeat", headers=b, json={
            "collector_state": "recording", "local_capture_policy": "recording",
            "collector_heartbeat_age_seconds": 60,
        })
        assert invalid.status_code == 400

    # An old report does NOT assert the recorder stopped or is still running.
    with db.connect() as conn:
        db._execute(conn, """
            UPDATE recorder_heartbeats SET received_at = ?
            WHERE organization_id = ? AND actor_id = ?
        """, ((datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),
              "tenant", "alice"))
    stale = _reported_recorder_state(db, organization_id="tenant", actor_id="alice")
    assert stale["local_recorder_state"] == "unknown_to_hosted_connector"
    assert stale["stale_report_is_not_proof_recorder_stopped"] is True


def test_local_collector_status_uses_real_recent_heartbeat(monkeypatch):
    from server import main
    original = dict(main.COLLECTOR_STATUS)
    try:
        main.COLLECTOR_STATUS.clear()
        monkeypatch.setattr("shared.capture_control.read_state",
                            lambda: {"state": "recording"})
        missing = main.collector_health()
        assert missing["collector_state"] == "no_recent_collector_heartbeat"
        main.COLLECTOR_STATUS["received_at"] = datetime.now(timezone.utc).isoformat()
        live = main.collector_health()
        assert live["collector_state"] == "recording"
        assert live["recent_collector_heartbeat"] is True
        assert "window_title" not in str(live)
        monkeypatch.setattr("shared.capture_control.read_state",
                            lambda: {"state": "paused"})
        assert main.collector_health()["collector_state"] == "paused"
    finally:
        main.COLLECTOR_STATUS.clear()
        main.COLLECTOR_STATUS.update(original)


def test_local_health_probe_has_no_gateway_bearer_or_work_contents(monkeypatch):
    from connector import sync

    requests = []
    class LocalResponse:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {
                "collector_state": "recording",
                "local_capture_policy": "recording",
                "collector_heartbeat_age_seconds": 5,
            }

    class LocalClient:
        def __init__(self, **kwargs):
            requests.append(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def get(self, url):
            requests.append({"url": url})
            return LocalResponse()

    monkeypatch.setattr(sync.httpx, "Client", LocalClient)
    assert sync._local_recorder_health()["collector_state"] == "recording"
    assert requests[0]["trust_env"] is False
    assert requests[1]["url"] == "http://127.0.0.1:8787/v1/collector-health"
    assert not any("Authorization" in str(item) or "Bearer" in str(item) for item in requests)
