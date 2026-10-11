from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.auth import DEVICE_SCOPES
from gateway.db import GatewayDB
from gateway.settings import GatewaySettings
from gateway import work_text_cloud as cloud


@pytest.fixture
def setup_cloud(monkeypatch, tmp_path):
    monkeypatch.setenv("OWG_CLOUD_WORK_TEXT_ENABLED", "1")
    db = GatewayDB("sqlite:///" + str(tmp_path / "gateway.db"))
    db.init()
    cloud.init_schema(db)
    one, two = "oauth-sub:alice", "oauth-sub:bob"
    # The desktop bearer never confers cloud-content permission on its own.
    # These fixture approvals stand in for an independent signed-in OAuth
    # account action, not a device-supplied timestamp.
    cloud.reauthorize_account(db, organization_id=one, actor_id=one,
                              consent_version=cloud.CONSENT_VERSION)
    cloud.reauthorize_account(db, organization_id=two, actor_id=two,
                              consent_version=cloud.CONSENT_VERSION)
    for token, actor in (("alice-device", one), ("bob-device", two)):
        db.put_token(
            token_id=token, token=token, token_type="device",
            organization_id=actor, actor_id=actor,
            device_id="desk-"+actor, scopes=set(DEVICE_SCOPES),
        )
        cloud.approve_device(db, organization_id=actor, actor_id=actor,
                             device_id="desk-"+actor,
                             consent_version=cloud.CONSENT_VERSION)
    app = create_app(
        settings=GatewaySettings(database_url=db.database_url, admin_token="admin",
                                 enrollment_token="enroll"), db=db
    )
    return db, app, one, two


def _time(minutes=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _entry(text="Synthetic planning notes for the quarterly review."):
    return {
        "ref": "owg:wt:10",
        "observed_at": _time(),
        "hostname": "docs.example.org",
        "page_title": "Quarterly review",
        "kind": "page",
        "redacted_text": text,
    }


def _payload(items=None, refs=None):
    return {
        "items": items if items is not None else [],
        "active_refs": refs if refs is not None else ["owg:wt:10"],
        "consent_version": cloud.CONSENT_VERSION,
        "granted_at": _time(-1),
    }


def test_cloud_sync_search_revoke_and_actor_isolation(setup_cloud):
    db, app, actor, other = setup_cloud
    token = {"Authorization": "Bearer alice-device"}
    wrong = {"Authorization": "Bearer bob-device"}
    with TestClient(app) as client:
        assert client.post("/v1/device/work-text-sync", json=_payload()).status_code == 401
        empty = client.post("/v1/device/work-text-sync", json=_payload(), headers=token)
        assert empty.status_code == 200, empty.text
        assert empty.json()["missing_refs"] == ["owg:wt:10"]
        content = client.post("/v1/device/work-text-sync",
                              json=_payload(items=[_entry()]), headers=token)
        assert content.status_code == 200, content.text
        assert content.json()["missing_refs"] == []
        result = cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")
        assert result["status"] == "ready" and len(result["results"]) == 1
        ref = result["results"][0]["reference"]
        # Search intentionally includes a bounded preview. It must not
        # expose the raw storage field or the device/local SQLite identity.
        assert "redacted_text" not in result["results"][0]
        assert "Synthetic" in result["results"][0]["preview"]
        assert "owg:wt:" not in result["results"][0]["reference"]
        full = cloud.excerpt(
            db, organization_id=actor, actor_id=actor, reference=ref
        )["excerpt"]
        assert "Synthetic planning" in full["redacted_text"]
        assert "item_ref" not in full  # Never reveal local SQLite row identifiers.
        assert cloud.search(db, organization_id=other, actor_id=other,
                            query="quarterly")["results"] == []
        assert cloud.excerpt(db, organization_id=other, actor_id=other,
                             reference=ref)["status"] == "not_enabled"
        # Other device cannot purge another actor's content.
        assert client.post("/v1/device/work-text-revoke", headers=wrong).status_code == 200
        assert cloud.search(db, organization_id=actor, actor_id=actor,
                            query="quarterly")["results"]
        assert client.post("/v1/device/work-text-revoke", headers=token).status_code == 200
        assert cloud.search(db, organization_id=actor, actor_id=actor,
                            query="quarterly")["status"] == "not_enabled"


def test_cloud_manifest_deletions_and_new_grant_clear_old_corpus(setup_cloud):
    db, app, actor, _ = setup_cloud
    p = db.authenticate("alice-device")
    epoch = _time(-1)
    first = {**_payload(items=[_entry()], refs=["owg:wt:10"]), "granted_at": epoch}
    cloud.sync(db, p, **first)
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["results"]
    cloud.sync(db, p, **{**_payload(items=[], refs=[]), "granted_at": epoch})
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["results"] == []
    cloud.sync(db, p, **first)
    # A new explicit consent epoch cannot silently resurrect old cloud content.
    changed = _payload(items=[], refs=[])
    changed["granted_at"] = _time()
    cloud.sync(db, p, **changed)
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["results"] == []


def test_cloud_uploads_are_disabled_by_default_and_enterprise_denied(setup_cloud, monkeypatch):
    db, _app, actor, _ = setup_cloud
    monkeypatch.delenv("OWG_CLOUD_WORK_TEXT_ENABLED")
    with pytest.raises(RuntimeError, match="unavailable"):
        cloud.sync(db, db.authenticate("alice-device"), **_payload())
    with pytest.raises(RuntimeError, match="unavailable"):
        cloud.search(db, organization_id=actor, actor_id=actor, query="review")
    monkeypatch.setenv("OWG_CLOUD_WORK_TEXT_ENABLED", "1")
    db.put_token(token_id="corp", token="corp", token_type="device",
                 organization_id="corp", actor_id="employee",
                 device_id="desk", scopes=set(DEVICE_SCOPES))
    with pytest.raises(PermissionError):
        cloud.sync(db, db.authenticate("corp"), **_payload())


def test_cloud_rejects_secrets_bad_hosts_and_preconsent_items(setup_cloud):
    db, _app, _, _ = setup_cloud
    p = db.authenticate("alice-device")
    for bad in (
        _entry("api_key = secretabc12345"),
        {**_entry(), "page_title": "API_KEY=secretvalue123456"},
        {**_entry(), "hostname": "accounts.google.com"},
        {**_entry(), "hostname": "192.168.1.1"},
        {**_entry(), "observed_at": _time(-30)},
    ):
        with pytest.raises(ValueError):
            cloud.sync(db, p, **_payload(items=[bad]))
    assert cloud.sync(db, p, **_payload(items=[_entry()]))["uploaded"] == 1


def test_server_rejects_unacknowledged_legacy_consent(setup_cloud):
    db, _app, _, _ = setup_cloud
    p = db.authenticate("alice-device")
    with pytest.raises(PermissionError, match="acknowledgement"):
        cloud.sync(db, p, **{**_payload(), "consent_version": "older-version"})


def test_remote_revocation_blocks_delayed_replay_until_new_user_grant(setup_cloud):
    db, _app, actor, _ = setup_cloud
    device = db.authenticate("alice-device")
    prior_epoch = _time(-3)
    old_batch = {**_payload(items=[_entry()], refs=["owg:wt:10"]), "granted_at": prior_epoch}
    cloud.sync(db, device, **old_batch)
    cloud.revoke(db, device)

    # An old queued request is never allowed to recreate deleted content.
    with pytest.raises(PermissionError, match="device-specific"):
        cloud.sync(db, device, **old_batch)
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["status"] == "not_enabled"

    # A new explicit consent epoch later than the server-side revoke can start
    # a new, empty corpus. Older snapshot timestamps cannot be retro-uploaded.
    future_grant = _time(1)
    # A fabricated fresh device timestamp is insufficient; explicit OAuth
    # approval of this enrolled device must occur again after revocation.
    with pytest.raises(PermissionError, match="device-specific"):
        cloud.sync(db, device, items=[], active_refs=[],
                   granted_at=future_grant, consent_version=cloud.CONSENT_VERSION)
    cloud.approve_device(db, organization_id=actor, actor_id=actor,
                         device_id=device.device_id,
                         consent_version=cloud.CONSENT_VERSION)
    assert cloud.sync(db, device, **{
        "items": [], "active_refs": [], "granted_at": future_grant,
        "consent_version": cloud.CONSENT_VERSION,
    })["status"] == "synced"
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["results"] == []


def test_cloud_storage_performs_second_redaction_without_local_identity_files(setup_cloud, monkeypatch):
    db, _app, actor, _ = setup_cloud
    import sys
    # Gateway must not import/read desktop user's local AI settings at ingest.
    # Simulate that module being unavailable in the hosted environment.
    import types
    old = sys.modules.get("server.ai_context")
    replacement = types.ModuleType("server.ai_context")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("desktop-only AI configuration must not run in Gateway")
    replacement.redact_contextually = forbidden
    monkeypatch.setitem(sys.modules, "server.ai_context", replacement)
    p = db.authenticate("alice-device")
    payload = {**_payload(items=[
        _entry("Email jane.doe@example.org about quarterly planning.")
    ])}
    cloud.sync(db, p, **payload)
    result = cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")
    ref = result["results"][0]["reference"]
    body = cloud.excerpt(db, organization_id=actor, actor_id=actor, reference=ref)["excerpt"]["redacted_text"]
    assert "jane.doe@example.org" not in body
    assert "quarterly" in body.lower()


def test_cloud_endpoint_rejects_oversized_body_before_json_model_parsing(setup_cloud):
    _db, app, _actor, _other = setup_cloud
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer alice-device",
                   "Content-Type": "application/json"}
        response = client.post("/v1/device/work-text-sync", headers=headers,
                               content=b" " * 200_000)
        assert response.status_code == 413
        response = client.post("/v1/device/work-text-sync", headers=headers,
                               content=b"{broken")
        assert response.status_code == 400
        valid = client.post("/v1/device/work-text-sync", headers=headers,
                            json=_payload(items=[_entry()]))
        assert valid.status_code == 200, valid.text


def test_cloud_device_authorization_is_actor_bound(setup_cloud):
    db, _app, actor, other = setup_cloud
    device = db.authenticate("alice-device")
    cloud.revoke(db, device)
    with pytest.raises(PermissionError, match="device"):
        cloud.approve_device(db, organization_id=actor, actor_id=actor,
                             device_id="nonexistent-device",
                             consent_version=cloud.CONSENT_VERSION)
    with pytest.raises(PermissionError, match="device"):
        cloud.approve_device(db, organization_id=other, actor_id=other,
                             device_id=device.device_id,
                             consent_version=cloud.CONSENT_VERSION)
    with pytest.raises(PermissionError, match="device-specific"):
        cloud.sync(db, device, **{**_payload(), "granted_at": _time(1)})


def test_cloud_account_quota_failure_rolls_back_new_rows(setup_cloud, monkeypatch):
    db, _app, actor, _ = setup_cloud
    device = db.authenticate("alice-device")
    monkeypatch.setattr(cloud, "MAX_ACCOUNT_ROWS", 1)
    first = {**_payload(items=[_entry()], refs=["owg:wt:10"]), "granted_at": _time(-1)}
    cloud.sync(db, device, **first)
    extra = {**_entry(), "ref": "owg:wt:11", "page_title": "Second task"}
    with pytest.raises(ValueError, match="quota"):
        cloud.sync(db, device, **{
            **_payload(items=[extra], refs=["owg:wt:10", "owg:wt:11"]),
            "granted_at": first["granted_at"],
        })
    found = cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")
    assert len(found["results"]) == 1
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="Second")["results"] == []


def test_cloud_account_character_quota_is_enforced(setup_cloud, monkeypatch):
    db, _app, actor, _ = setup_cloud
    device = db.authenticate("alice-device")
    monkeypatch.setattr(cloud, "MAX_ACCOUNT_TEXT_CHARS", 16)
    with pytest.raises(ValueError, match="quota"):
        cloud.sync(db, device, **_payload(items=[_entry()]))
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["results"] == []


def test_cloud_device_upload_rate_limit_is_isolated_to_text_endpoint(setup_cloud):
    _db, app, _actor, _other = setup_cloud
    headers = {"Authorization": "Bearer alice-device"}
    with TestClient(app) as client:
        for _ in range(30):
            response = client.post("/v1/device/work-text-sync",
                                   json=_payload(items=[], refs=[]), headers=headers)
            assert response.status_code == 200, response.text
        throttled = client.post("/v1/device/work-text-sync",
                                json=_payload(items=[], refs=[]), headers=headers)
        assert throttled.status_code == 429
        assert int(throttled.headers["Retry-After"]) >= 1
        assert client.get("/health").status_code == 200
