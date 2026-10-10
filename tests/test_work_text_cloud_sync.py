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
    for token, actor in (("alice-device", one), ("bob-device", two)):
        db.put_token(
            token_id=token, token=token, token_type="device",
            organization_id=actor, actor_id=actor,
            device_id="desk-"+actor, scopes=set(DEVICE_SCOPES),
        )
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
        assert "Synthetic" not in str(result)
        assert "Synthetic planning" in cloud.excerpt(
            db, organization_id=actor, actor_id=actor, reference=ref
        )["excerpt"]["redacted_text"]
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
    first = _payload(items=[_entry()], refs=["owg:wt:10"])
    cloud.sync(db, p, **first)
    assert cloud.search(db, organization_id=actor, actor_id=actor, query="quarterly")["results"]
    cloud.sync(db, p, **_payload(items=[], refs=[]))
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
