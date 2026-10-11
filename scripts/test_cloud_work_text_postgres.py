"""Real PostgreSQL cloud-text authorization and concurrency smoke test.

Run inside the gateway container against a disposable CI PostgreSQL database,
with OWG_CLOUD_WORK_TEXT_ENABLED=1. This is intentionally independent of pytest.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

from gateway.auth import DEVICE_SCOPES
from gateway.db import GatewayDB
from gateway import work_text_cloud as cloud


def _now(offset=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=offset)).isoformat()


def main():
    assert os.environ.get("OWG_CLOUD_WORK_TEXT_ENABLED") == "1"
    db = GatewayDB(os.environ["OWG_GATEWAY_DATABASE_URL"])
    assert db.is_postgres, "concurrency test MUST use real PostgreSQL"
    db.init()
    cloud.init_schema(db)
    actor = "oauth-sub:pg-cloud-" + uuid4().hex
    devices = ("pg-device-a", "pg-device-b")
    grant_at = _now(-2)
    cloud.reauthorize_account(
        db, organization_id=actor, actor_id=actor,
        consent_version=cloud.CONSENT_VERSION,
    )
    for device in devices:
        db.put_token(
            token_id=device + actor, token=device + actor, token_type="device",
            organization_id=actor, actor_id=actor, device_id=device,
            scopes=set(DEVICE_SCOPES),
        )
        cloud.approve_device(
            db, organization_id=actor, actor_id=actor, device_id=device,
            consent_version=cloud.CONSENT_VERSION,
        )
    principals = [db.authenticate(device + actor) for device in devices]

    # The actor-level mutex must serialize concurrent writers so the
    # account-wide quota cannot be overrun through two different devices.
    start = Barrier(2)
    cloud.MAX_ACCOUNT_ROWS = 1

    def upload(i):
        start.wait(timeout=15)
        entry = {
            "ref": "owg:wt:1", "observed_at": _now(),
            "hostname": "docs.example.org", "page_title": "Synthetic planning",
            "kind": "page", "redacted_text": "Synthetic quarterly work plan.",
        }
        try:
            cloud.sync(
                db, principals[i], items=[entry], active_refs=["owg:wt:1"],
                consent_version=cloud.CONSENT_VERSION, granted_at=grant_at,
            )
            return "ok"
        except ValueError as exc:
            assert "quota" in str(exc), exc
            return "quota"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(upload, (0, 1)))
    assert sorted(outcomes) == ["ok", "quota"], outcomes

    with db.connect() as conn:
        row = db._execute(
            conn, "SELECT COUNT(*) FROM cloud_work_text WHERE organization_id=?",
            (actor,),
        ).fetchone()
        assert row[0] == 1, row

    # A concurrent deletion must win the final state irrespective of which
    # operation acquired the PostgreSQL actor row lock first.
    start = Barrier(2)

    def replay_upload():
        start.wait(timeout=15)
        try:
            cloud.sync(
                db, principals[0], items=[], active_refs=[],
                consent_version=cloud.CONSENT_VERSION, granted_at=grant_at,
            )
            return "ok"
        except PermissionError:
            return "revoked"

    def delete():
        start.wait(timeout=15)
        return cloud.delete_account(
            db, organization_id=actor, actor_id=actor
        )["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(replay_upload), pool.submit(delete)]
        outcomes = [f.result(timeout=20) for f in futures]
    assert outcomes[1] == "deleted", outcomes
    assert outcomes[0] in {"ok", "revoked"}, outcomes
    with db.connect() as conn:
        assert db._execute(
            conn, "SELECT COUNT(*) FROM cloud_work_text WHERE organization_id=?",
            (actor,),
        ).fetchone()[0] == 0

    # After deletion, a device cannot restore content by claiming a
    # freshly fabricated local timestamp, including after OAuth account
    # reauthorization without a new device-specific OAuth grant.
    try:
        cloud.sync(
            db, principals[0], items=[], active_refs=[],
            consent_version=cloud.CONSENT_VERSION, granted_at=_now(),
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("account deletion must reject forged device consent")
    cloud.reauthorize_account(
        db, organization_id=actor, actor_id=actor,
        consent_version=cloud.CONSENT_VERSION,
    )
    try:
        cloud.sync(
            db, principals[0], items=[], active_refs=[],
            consent_version=cloud.CONSENT_VERSION, granted_at=_now(),
        )
    except PermissionError as exc:
        assert "device-specific" in str(exc), exc
    else:
        raise AssertionError("old device token reauthorized itself after account deletion")

    print("Cloud-text PostgreSQL actor-lock, quota, deletion and grant tests passed")


if __name__ == "__main__":
    main()
