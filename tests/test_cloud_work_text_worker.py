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
    cloud.init_schema(db)
    cloud.reauthorize_account(db, organization_id=org, actor_id=org,
                              consent_version=cloud.CONSENT_VERSION)
    db.put_token(token_id="desktop", token="desktop-token", token_type="device",
                 organization_id=org, actor_id=org, device_id="desktop-one",
                 scopes=set(DEVICE_SCOPES))
    cloud.approve_device(db, organization_id=org, actor_id=org,
                         device_id="desktop-one", consent_version=cloud.CONSENT_VERSION)
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


def test_account_delete_revokes_local_cloud_toggle_and_never_retries_stale_batch(tmp_path, monkeypatch):
    from server import work_text_capture, ai_access
    from shared import capture_control

    monkeypatch.setenv("OWG_CLOUD_WORK_TEXT_ENABLED", "1")
    org = "oauth-sub:delete-test"
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    db.init()
    cloud.init_schema(db)
    cloud.reauthorize_account(db, organization_id=org, actor_id=org,
                              consent_version=cloud.CONSENT_VERSION)
    db.put_token(token_id="del-device", token="del-token", token_type="device",
                 organization_id=org, actor_id=org, device_id="local-device",
                 scopes=set(DEVICE_SCOPES))
    cloud.approve_device(db, organization_id=org, actor_id=org,
                         device_id="local-device", consent_version=cloud.CONSENT_VERSION)
    app = create_app(settings=GatewaySettings(database_url=db.database_url,
                    admin_token="admin", enrollment_token="enroll"), db=db)
    state = SyncState(tmp_path / "sync.db")
    now = datetime.now(timezone.utc)
    consent = (now - timedelta(minutes=2)).isoformat()
    policy = {"capture_enabled": True, "ai_read_enabled": True,
              "cloud_read_enabled": True, "cloud_ack_version": cloud.CONSENT_VERSION,
              "cloud_granted_at": consent}
    entry = {"ref": "owg:wt:7", "observed_at": now.isoformat(),
             "hostname": "docs.example.org", "page_title": "Synthetic task",
             "kind": "page", "redacted_text": "Quarterly synthetic task document"}
    monkeypatch.setattr(work_text_capture, "get_policy", lambda: dict(policy))
    def update_local(change):
        policy.update(change)
        return dict(policy)
    monkeypatch.setattr(work_text_capture, "set_policy", update_local)
    monkeypatch.setattr(work_text_capture, "cloud_sync_snapshot", lambda: {
        "items": [entry], "granted_at": consent, "consent_version": cloud.CONSENT_VERSION,
    })
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    monkeypatch.setattr(worker, "_NEXT_SCAN", 0.0)

    with TestClient(app, headers={"Authorization": "Bearer del-token"}) as client:
        worker.process_cloud_text(client, url="http://testserver", state=state, now=10.0)
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=22.0) == 1
        assert state.get_bool("cloud_text_remote_granted")
        assert cloud.delete_account(db, organization_id=org, actor_id=org)["deleted"] == 1
        # Periodic reconciliation discovers revocation; no standing poll is
        # added while the local cloud preference is disabled.
        state.set("cloud_text_last_check", "-3600")
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=40.0) == 0
        assert policy["cloud_read_enabled"] is False
        assert not state.get_bool("cloud_text_remote_granted")
        assert "fresh account and local approval" in state.get("cloud_text_last_error")
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=60.0) == 0


def test_disabled_cloud_grant_revokes_even_if_worker_sync_state_was_reset(tmp_path, monkeypatch):
    """Do not strand a readable cloud corpus after reinstall or state DB loss."""
    from server import work_text_capture, ai_access
    from shared import capture_control
    monkeypatch.setenv("OWG_CLOUD_WORK_TEXT_ENABLED", "1")
    actor = "oauth-sub:restore-test"
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    db.init()
    cloud.init_schema(db)
    cloud.reauthorize_account(db, organization_id=actor, actor_id=actor,
                              consent_version=cloud.CONSENT_VERSION)
    db.put_token(token_id="device", token="valid-device", token_type="device",
                 organization_id=actor, actor_id=actor, device_id="linked-one",
                 scopes=set(DEVICE_SCOPES))
    cloud.approve_device(db, organization_id=actor, actor_id=actor,
                         device_id="linked-one", consent_version=cloud.CONSENT_VERSION)
    app = create_app(settings=GatewaySettings(database_url=db.database_url,
                     admin_token="", enrollment_token=""), db=db)
    start = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    item = {
        "ref": "owg:wt:5", "observed_at": datetime.now(timezone.utc).isoformat(),
        "hostname": "docs.example.org", "page_title": "Planning",
        "kind": "page", "redacted_text": "Synthetic quarterly business plan.",
    }
    cloud.sync(db, db.authenticate("valid-device"), items=[item], active_refs=["owg:wt:5"],
               consent_version=cloud.CONSENT_VERSION, granted_at=start)
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["results"]
    # The user's local privacy policy survives; the optional sync-state DB did not.
    policy = {
        "capture_enabled": True, "ai_read_enabled": True, "cloud_read_enabled": False,
        "cloud_ack_version": cloud.CONSENT_VERSION, "cloud_granted_at": start,
    }
    monkeypatch.setattr(work_text_capture, "get_policy", lambda: dict(policy))
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    reset_state = SyncState(tmp_path / "fresh-worker-state.db")
    with TestClient(app, headers={"Authorization": "Bearer valid-device"}) as client:
        assert worker.process_cloud_text(client, url="http://testserver", state=reset_state) == 0
        assert reset_state.get("cloud_text_last_revoked_grant") == start
        assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["status"] == "not_enabled"

        class NoRequest:
            def post(self, *_args, **_kwargs):
                raise AssertionError("already confirmed revocation must not repeat")
        assert worker.process_cloud_text(NoRequest(), url="http://testserver", state=reset_state) == 0


def test_missing_oauth_account_approval_does_not_spam_gateway_or_disable_local_choice(tmp_path, monkeypatch):
    """Missing account approval is informative, bounded and non-destructive."""
    from server import work_text_capture, ai_access
    from shared import capture_control
    monkeypatch.setenv("OWG_CLOUD_WORK_TEXT_ENABLED", "1")
    actor = "oauth-sub:needs-approval"
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    db.init()
    db.put_token(token_id="needs-approval", token="needs-token", token_type="device",
                 organization_id=actor, actor_id=actor, device_id="desktop",
                 scopes=set(DEVICE_SCOPES))
    app = create_app(settings=GatewaySettings(database_url=db.database_url,
                     admin_token="", enrollment_token=""), db=db)
    epoch = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    settings = {"capture_enabled": True, "ai_read_enabled": True,
                "cloud_read_enabled": True, "cloud_ack_version": cloud.CONSENT_VERSION,
                "cloud_granted_at": epoch}
    monkeypatch.setattr(work_text_capture, "get_policy", lambda: dict(settings))
    monkeypatch.setattr(work_text_capture, "cloud_sync_snapshot", lambda: {
        "items": [], "granted_at": epoch, "consent_version": cloud.CONSENT_VERSION
    })
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    state = SyncState(tmp_path / "worker.db")
    monkeypatch.setattr(worker, "_NEXT_SCAN", 0.0)
    with TestClient(app, headers={"Authorization": "Bearer needs-token"}) as client:
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=10) == 0
        assert "signed-in account approval" in state.get("cloud_text_last_error")
        assert settings["cloud_read_enabled"] is True
        assert worker._NEXT_SCAN == 310.0

        class NoNetwork:
            def post(self, *_args, **_kwargs):
                raise AssertionError("approval-denied retry must be throttled")
        assert worker.process_cloud_text(NoNetwork(), url="http://testserver",
                                         state=state, now=12) == 0
        cloud.reauthorize_account(db, organization_id=actor, actor_id=actor,
                                  consent_version=cloud.CONSENT_VERSION)
        cloud.approve_device(db, organization_id=actor, actor_id=actor,
                             device_id="desktop", consent_version=cloud.CONSENT_VERSION)
        assert worker.process_cloud_text(client, url="http://testserver",
                                         state=state, now=311) == 0
        assert state.get_bool("cloud_text_remote_granted") is True


def test_pausing_cloud_sharing_revokes_and_requires_fresh_local_opt_in(tmp_path, monkeypatch):
    """Do not silently resume with a server-revoked consent epoch."""
    from server import work_text_capture as wt, ai_access
    from shared import capture_control
    monkeypatch.setenv("OWG_CLOUD_WORK_TEXT_ENABLED", "1")
    actor = "oauth-sub:pause-example"
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    db.init()
    cloud.init_schema(db)
    cloud.reauthorize_account(db, organization_id=actor, actor_id=actor,
                              consent_version=cloud.CONSENT_VERSION)
    db.put_token(token_id="pause-device", token="pause-token", token_type="device",
                 organization_id=actor, actor_id=actor, device_id="desktop",
                 scopes=set(DEVICE_SCOPES))
    cloud.approve_device(db, organization_id=actor, actor_id=actor,
                         device_id="desktop", consent_version=cloud.CONSENT_VERSION)
    app = create_app(settings=GatewaySettings(database_url=db.database_url,
                     admin_token="", enrollment_token=""), db=db)
    epoch = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    policy = {"capture_enabled": True, "ai_read_enabled": True,
              "cloud_read_enabled": True, "cloud_granted_at": epoch,
              "cloud_ack_version": cloud.CONSENT_VERSION}
    monkeypatch.setattr(wt, "get_policy", lambda: dict(policy))
    monkeypatch.setattr(wt, "set_policy", lambda change: policy.update(change) or dict(policy))
    monkeypatch.setattr(wt, "cloud_sync_snapshot", lambda: {
        "items": [], "granted_at": epoch, "consent_version": cloud.CONSENT_VERSION
    })
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    monkeypatch.setattr(worker, "_NEXT_SCAN", 0.0)
    state = SyncState(tmp_path / "worker.db")
    with TestClient(app, headers={"Authorization": "Bearer pause-token"}) as client:
        assert worker.process_cloud_text(client, url="http://testserver", state=state, now=5) == 0
        assert state.get_bool("cloud_text_remote_granted")
        assert policy["cloud_read_enabled"] is True
        assert worker.process_cloud_text(client, url="http://testserver",
                                         state=state, sharing_paused=True, now=6) == 0
        assert policy["cloud_read_enabled"] is False
        assert not state.get_bool("cloud_text_remote_granted")
        assert "fresh local cloud opt-in" in state.get("cloud_text_last_error")
        # Resume must not undo local revocation or send another batch.
        class NoNetwork:
            def post(self, *_args, **_kwargs):
                raise AssertionError("resume may not automatically re-share old content")
        assert worker.process_cloud_text(NoNetwork(), url="http://testserver",
                                         state=state, now=15) == 0
        assert policy["cloud_read_enabled"] is False


def test_operator_disabled_cloud_sync_backs_off_without_affecting_local_choice(tmp_path, monkeypatch):
    from server import work_text_capture, ai_access
    from shared import capture_control
    monkeypatch.delenv("OWG_CLOUD_WORK_TEXT_ENABLED", raising=False)
    actor = "oauth-sub:operator-off"
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    db.init()
    db.put_token(token_id="operator-off", token="operator-off",
                 token_type="device", organization_id=actor, actor_id=actor,
                 device_id="desktop", scopes=set(DEVICE_SCOPES))
    app = create_app(settings=GatewaySettings(database_url=db.database_url,
                     admin_token="", enrollment_token=""), db=db)
    grant = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    policy = {"capture_enabled": True, "ai_read_enabled": True,
              "cloud_read_enabled": True, "cloud_ack_version": cloud.CONSENT_VERSION,
              "cloud_granted_at": grant}
    monkeypatch.setattr(work_text_capture, "get_policy", lambda: dict(policy))
    monkeypatch.setattr(work_text_capture, "cloud_sync_snapshot", lambda: {
        "items": [], "consent_version": cloud.CONSENT_VERSION, "granted_at": grant
    })
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    monkeypatch.setattr(worker, "_NEXT_SCAN", 0.0)
    state = SyncState(tmp_path / "worker.db")
    with TestClient(app, headers={"Authorization": "Bearer operator-off"}) as client:
        assert worker.process_cloud_text(client, url="http://testserver",
                                         state=state, now=10) == 0
        assert "not available" in state.get("cloud_text_last_error")
        assert worker._NEXT_SCAN == 1810.0
        assert policy["cloud_read_enabled"] is True
        class NoRequest:
            def post(self, *_args, **_kwargs):
                raise AssertionError("operator disabled upload must back off")
        assert worker.process_cloud_text(NoRequest(), url="http://testserver",
                                         state=state, now=12) == 0
