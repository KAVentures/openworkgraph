from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from gateway.app import create_app
from gateway.auth import DEVICE_SCOPES
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings
from gateway import work_text_relay as relay


@pytest.fixture
def relay_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OWG_WORK_TEXT_RELAY_KEY", Fernet.generate_key().decode("ascii"))
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    db.init()
    relay.init_schema(db)
    a = "oauth-sub:test-primary"
    b = "oauth-sub:test-other"
    db.put_token(token_id="d1", token="device-one", token_type="device",
                 organization_id=a, actor_id=a, device_id="desktop-one", scopes=set(DEVICE_SCOPES))
    db.put_token(token_id="d2", token="device-two", token_type="device",
                 organization_id=b, actor_id=b, device_id="desktop-two", scopes=set(DEVICE_SCOPES))
    return db, a, b


def _row():
    return {
        "ref": "owg:wt:42",
        "observed_at": "2026-10-10T10:00:00+00:00",
        "hostname": "docs.example.com",
        "page_title": "Quarterly planning",
        "kind": "page",
        "redacted_text": "Synthetic rollout planning notes, no real customer data.",
    }


def test_relay_end_to_end_encrypted_and_actor_isolated(relay_setup):
    db, actor, other = relay_setup
    requested = relay.queue_search(db, organization_id=actor, actor_id=actor, query="rollout planning")
    assert requested["status"] == "pending"
    rid = requested["request_id"]
    with db.connect() as conn:
        row = conn.execute("SELECT query_ciphertext, response_ciphertext FROM work_text_relay WHERE request_id=?", (rid,)).fetchone()
    assert "rollout" not in row["query_ciphertext"]
    assert row["response_ciphertext"] is None

    p1 = db.authenticate("device-one")
    p2 = db.authenticate("device-two")
    assert relay.pending_for_device(db, p1)["requests"] == [{"request_id": rid, "query": "rollout planning"}]
    assert relay.pending_for_device(db, p2)["requests"] == []
    with pytest.raises(PermissionError):
        relay.answer_from_device(db, p2, rid, [_row()])
    assert relay.answer_from_device(db, p1, rid, [_row()])["status"] == "delivered"
    with pytest.raises(PermissionError):
        relay.answer_from_device(db, p1, rid, [_row()])

    preview = relay.read_result(db, organization_id=actor, actor_id=actor, request_id=rid)
    assert preview["status"] == "ready" and len(preview["results"]) == 1
    assert "redacted_text" not in json.dumps(preview)
    answer = relay.read_result(db, organization_id=actor, actor_id=actor, request_id=rid, excerpt_index=0)
    assert "Synthetic rollout" in answer["excerpt"]["redacted_text"]
    assert relay.read_result(db, organization_id=other, actor_id=other, request_id=rid)["status"] == "not_found_or_expired"
    with db.connect() as conn:
        item = conn.execute("SELECT response_ciphertext FROM work_text_relay WHERE request_id=?", (rid,)).fetchone()
        assert "Synthetic rollout" not in item["response_ciphertext"]


def test_relay_expiration_and_revoke(relay_setup):
    db, actor, _ = relay_setup
    rid = relay.queue_search(db, organization_id=actor, actor_id=actor, query="rollout planning")["request_id"]
    p = db.authenticate("device-one")
    assert relay.revoke_device(db, p)["requests_deleted"] == 1
    assert relay.pending_for_device(db, p)["requests"] == []
    assert relay.read_result(db, organization_id=actor, actor_id=actor, request_id=rid)["status"] == "not_found_or_expired"
    new = relay.queue_search(db, organization_id=actor, actor_id=actor, query="another task")["request_id"]
    yesterday = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE work_text_relay SET expires_at=? WHERE request_id=?", (yesterday, new))
    assert relay.read_result(db, organization_id=actor, actor_id=actor, request_id=new)["status"] == "not_found_or_expired"


def test_relay_requires_operator_key_and_personal_account(relay_setup, monkeypatch):
    db, actor, _ = relay_setup
    monkeypatch.delenv("OWG_WORK_TEXT_RELAY_KEY")
    with pytest.raises(RuntimeError, match="not configured"):
        relay.queue_search(db, organization_id=actor, actor_id=actor, query="planning")
    with pytest.raises(PermissionError):
        relay.queue_search(db, organization_id="enterprise", actor_id="employee", query="planning")


def test_real_gateway_http_route_rejects_unsigned_and_wrong_device(relay_setup):
    db, actor, _ = relay_setup
    settings = GatewaySettings(database_url=db.database_url, admin_token="admin-test", enrollment_token="enroll-test")
    app = create_app(settings=settings, db=db)
    with TestClient(app) as client:
        assert client.get("/v1/device/work-text-requests").status_code == 401
        queued = relay.queue_search(db, organization_id=actor, actor_id=actor, query="rollout")
        rid = queued["request_id"]
        d1 = {"Authorization": "Bearer device-one"}
        d2 = {"Authorization": "Bearer device-two"}
        one = client.get("/v1/device/work-text-requests", headers=d1)
        assert one.status_code == 200 and one.json()["requests"][0]["request_id"] == rid
        assert client.get("/v1/device/work-text-requests", headers=d2).json()["requests"] == []
        wrong = client.post("/v1/device/work-text-response", headers=d2, json={"request_id": rid, "items": [_row()]})
        assert wrong.status_code == 403
        ok = client.post("/v1/device/work-text-response", headers=d1, json={"request_id": rid, "items": [_row()]})
        assert ok.status_code == 200
        assert client.post("/v1/device/work-text-revoke", headers=d2).status_code == 200
        assert relay.read_result(db, organization_id=actor, actor_id=actor, request_id=rid)["status"] == "ready"
        assert client.post("/v1/device/work-text-revoke", headers=d1).status_code == 200
        assert relay.read_result(db, organization_id=actor, actor_id=actor, request_id=rid)["status"] == "not_found_or_expired"


def test_device_worker_answers_only_during_local_cloud_opt_in(relay_setup, monkeypatch):
    from connector import work_text_relay as worker
    from server import work_text_capture as local, ai_access

    db, actor, _ = relay_setup
    settings = GatewaySettings(database_url=db.database_url, admin_token="", enrollment_token="")
    app = create_app(settings=settings, db=db)
    permissions = {"capture_enabled": True, "ai_read_enabled": True, "cloud_read_enabled": True}
    monkeypatch.setattr(local, "get_policy", lambda: dict(permissions))
    monkeypatch.setattr(local, "search_for_cloud",
        lambda query, limit=4: {"items": [_row()], "source": "local_opt_in_browser_text"})
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    monkeypatch.setattr(worker, "_NEXT_CHECK", 0.0)

    class State:
        def __init__(self):
            self.data = {}
        def get_bool(self, name, default=False):
            return bool(self.data.get(name, default))
        def set_bool(self, name, value):
            self.data[name] = value
        def set(self, name, value):
            self.data[name] = value

    state = State()
    with TestClient(app, headers={"Authorization": "Bearer device-one"}) as client:
        requested = relay.queue_search(db, organization_id=actor, actor_id=actor, query="rollout")
        assert worker.process_pending(client, url="http://testserver", state=state, now=100.0) == 1
        assert relay.read_result(db, organization_id=actor, actor_id=actor,
                                 request_id=requested["request_id"])["status"] == "ready"
        assert state.get_bool("cloud_work_text_was_permitted")
        permissions["cloud_read_enabled"] = False
        assert worker.process_pending(client, url="http://testserver", state=state, now=101.0) == 0
        assert not state.get_bool("cloud_work_text_was_permitted")
        assert relay.read_result(db, organization_id=actor, actor_id=actor,
                                 request_id=requested["request_id"])["status"] == "not_found_or_expired"
        # No new text response or remote polling once the user revokes.
        second = relay.queue_search(db, organization_id=actor, actor_id=actor, query="rollout")
        assert worker.process_pending(client, url="http://testserver", state=state, now=200.0) == 0
        assert relay.read_result(db, organization_id=actor, actor_id=actor,
                                 request_id=second["request_id"])["status"] == "pending"
