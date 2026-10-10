from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from connector import work_text_sync as worker
from connector.state import SyncState
from gateway.app import create_app
from gateway.auth import DEVICE_SCOPES
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings
from gateway import work_text_cloud as cloud


def test_device_sync_and_revocation_use_existing_gateway_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OWG_CLOUD_WORK_TEXT_ENABLED", "1")
    org = "oauth-sub:person"
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    db.init()
    db.put_token(token_id="desktop", token="desktop-token", token_type="device",
                 organization_id=org, actor_id=org, device_id="desktop-one",
                 scopes=set(DEVICE_SCOPES))
    app = create_app(settings=GatewaySettings(database_url=db.database_url, admin_token="admin",
                                               enrollment_token="enroll"), db=db)
    state = SyncState(tmp_path / "sync.db")
    consent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    observed = datetime.now(timezone.utc).isoformat()
    setting = {
        "capture_enabled": True, "ai_read_enabled": True, "cloud_read_enabled": True,
        "cloud_ack_version": cloud.CONSENT_VERSION, "cloud_granted_at": consent,
    }
    entries = [{
        "ref": "owg:wt:1", "observed_at": observed, "hostname": "docs.example.org",
        "page_title": "Plan", "kind": "page",
        "redacted_text": "Quarterly task planning for a synthetic team.",
    }]
    from server import work_text_capture, ai_access
    from shared import capture_control
    monkeypatch.setattr(work_text_capture, "get_policy", lambda: dict(setting))
    monkeypatch.setattr(work_text_capture, "cloud_sync_snapshot", lambda: {
        "items": list(entries), "granted_at": consent, "consent_version": cloud.CONSENT_VERSION,
    })
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    monkeypatch.setattr(worker, "_NEXT_SCAN", 0.0)

    with TestClient(app, headers={"Authorization": "Bearer desktop-token"}) as client:
        # Manifest first, then upload only the 1 referenced new text item.
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=10) == 0
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=21) == 1
        result = cloud.search(db, organization_id=org, actor_id=org, query="Quarterly")
        assert len(result["results"]) == 1
        assert state.get_bool("cloud_text_remote_granted")
        # Removing the local row reconciles a remote deletion without reupload.
        entries.clear()
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=34) == 0
        assert cloud.search(db, organization_id=org, actor_id=org, query="Quarterly")["results"] == []
        # Disabling sharing sends deletion even if existing structural sync is paused.
        setting["cloud_read_enabled"] = False
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=35) == 0
        assert not state.get_bool("cloud_text_remote_granted")
        assert cloud.search(db, organization_id=org, actor_id=org, query="Quarterly")["status"] == "not_enabled"


def test_worker_requires_consent_and_no_data_upload_while_disabled(tmp_path, monkeypatch):
    from server import work_text_capture, ai_access
    monkeypatch.setattr(work_text_capture, "get_policy", lambda: {
        "capture_enabled": True, "ai_read_enabled": True,
        "cloud_read_enabled": False, "cloud_ack_version": "",
    })
    monkeypatch.setattr(work_text_capture, "cloud_sync_snapshot",
                        lambda: (_ for _ in ()).throw(AssertionError("must not read content")))
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    state = SyncState(tmp_path / "state.db")
    class NoNetwork:
        def post(self, *_args, **_kwargs):
            raise AssertionError("no network request when never opted in")
    assert worker.process_cloud_text(NoNetwork(), url="https://invalid.test", state=state, now=10) == 0
