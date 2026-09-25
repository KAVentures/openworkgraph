from __future__ import annotations

"""Additive Gateway storage for signed enterprise declared-policy bundles."""

import json
from datetime import datetime, timezone
from typing import Any

from shared.policy_bundle import PolicyBundleError, inspect_policy_bundle

from .db import GatewayDB


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_declared_policy_bundles (
  organization_id TEXT NOT NULL,
  bundle_sha256 TEXT NOT NULL,
  policy_revision INTEGER NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  key_id TEXT NOT NULL,
  bundle_json TEXT NOT NULL,
  published_at TEXT NOT NULL,
  PRIMARY KEY (organization_id, bundle_sha256)
);
CREATE INDEX IF NOT EXISTS idx_gateway_declared_policy_history
  ON organization_declared_policy_bundles(organization_id, published_at DESC);
CREATE TABLE IF NOT EXISTS organization_declared_policy_current (
  organization_id TEXT PRIMARY KEY,
  bundle_sha256 TEXT NOT NULL,
  selected_at TEXT NOT NULL
);
"""

_POSTGRES_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS organization_declared_policy_bundles (
      organization_id TEXT NOT NULL,
      bundle_sha256 TEXT NOT NULL,
      policy_revision BIGINT NOT NULL,
      manifest_sha256 TEXT NOT NULL,
      key_id TEXT NOT NULL,
      bundle_json TEXT NOT NULL,
      published_at TEXT NOT NULL,
      PRIMARY KEY (organization_id, bundle_sha256)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_gateway_declared_policy_history
      ON organization_declared_policy_bundles(organization_id, published_at DESC)""",
    """CREATE TABLE IF NOT EXISTS organization_declared_policy_current (
      organization_id TEXT PRIMARY KEY,
      bundle_sha256 TEXT NOT NULL,
      selected_at TEXT NOT NULL
    )""",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_declared_policy_store(db: GatewayDB) -> None:
    with db.connect() as conn:
        if db.is_postgres:
            for statement in _POSTGRES_STATEMENTS:
                conn.execute(statement)
        else:
            conn.executescript(_SQLITE_SCHEMA)


def _decode_row(db: GatewayDB, row: Any, columns: list[str] | None) -> dict[str, Any] | None:
    data = db._row(row, columns)
    if not data:
        return None
    try:
        bundle = json.loads(data.get("bundle_json") or "{}")
    except Exception as exc:
        raise RuntimeError("stored declared-policy bundle is invalid JSON") from exc
    if not isinstance(bundle, dict):
        raise RuntimeError("stored declared-policy bundle is invalid")
    return {
        "organization_id": str(data.get("organization_id") or ""),
        "policy_revision": int(data.get("policy_revision") or 0),
        "bundle_sha256": str(data.get("bundle_sha256") or ""),
        "manifest_sha256": str(data.get("manifest_sha256") or ""),
        "key_id": str(data.get("key_id") or ""),
        "published_at": str(data.get("published_at") or ""),
        "selected_at": str(data.get("selected_at") or ""),
        "bundle": bundle,
    }


def latest_declared_policy_bundle(db: GatewayDB, organization_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT b.organization_id, b.policy_revision, b.bundle_sha256, b.manifest_sha256,
                      b.key_id, b.bundle_json, b.published_at, c.selected_at
               FROM organization_declared_policy_current c
               JOIN organization_declared_policy_bundles b
                 ON b.organization_id = c.organization_id AND b.bundle_sha256 = c.bundle_sha256
               WHERE c.organization_id = ?
               LIMIT 1""",
            (organization_id,),
        )
        row = cur.fetchone()
        columns = [item[0] for item in cur.description] if cur.description else None
    return _decode_row(db, row, columns)


def publish_declared_policy_bundle(
    db: GatewayDB,
    *,
    organization_id: str,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Store/select a structurally valid signed envelope for device distribution.

    The Gateway deliberately does not decide signature authenticity or rollback.
    A compromised Gateway admin can disrupt distribution but cannot create a bundle
    that a correctly pinned endpoint will accept as organizational policy. Keeping
    the Gateway as a current-pointer transport also allows recovery by republishing
    a valid signed bundle after an invalid/poisoned publication.
    """
    inspected = inspect_policy_bundle(bundle)
    if inspected["organization_id"] != organization_id:
        raise PolicyBundleError("policy bundle belongs to another organization")

    normalized = inspected["bundle"]
    revision = int(inspected["policy_revision"])
    bundle_sha = str(inspected["bundle_sha256"])
    manifest_sha = str(inspected["manifest_sha256"])
    key_id = str(inspected["key_id"])
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    previous = latest_declared_policy_bundle(db, organization_id)
    idempotent = bool(previous and previous["bundle_sha256"] == bundle_sha)
    now = _now()

    with db.connect() as conn:
        db._execute(
            conn,
            """INSERT INTO organization_declared_policy_bundles(
                 organization_id, bundle_sha256, policy_revision, manifest_sha256, key_id, bundle_json, published_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(organization_id, bundle_sha256) DO NOTHING""",
            (organization_id, bundle_sha, revision, manifest_sha, key_id, payload, now),
        )
        if db.is_postgres:
            current_sql = """INSERT INTO organization_declared_policy_current(organization_id, bundle_sha256, selected_at)
                             VALUES (?, ?, ?)
                             ON CONFLICT(organization_id) DO UPDATE
                             SET bundle_sha256 = EXCLUDED.bundle_sha256, selected_at = EXCLUDED.selected_at"""
        else:
            current_sql = """INSERT INTO organization_declared_policy_current(organization_id, bundle_sha256, selected_at)
                             VALUES (?, ?, ?)
                             ON CONFLICT(organization_id) DO UPDATE
                             SET bundle_sha256 = excluded.bundle_sha256, selected_at = excluded.selected_at"""
        db._execute(conn, current_sql, (organization_id, bundle_sha, now))

    selected = latest_declared_policy_bundle(db, organization_id)
    if selected is None or selected["bundle_sha256"] != bundle_sha:
        raise RuntimeError("unable to select published declared-policy bundle")
    return {
        **selected,
        "idempotent": idempotent,
        "signature_verified_by_gateway": False,
        "rollback_enforced_by_gateway": False,
    }


def declared_policy_history(db: GatewayDB, organization_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    cap = max(1, min(int(limit), 200))
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT organization_id, policy_revision, bundle_sha256, manifest_sha256, key_id,
                      bundle_json, published_at, '' AS selected_at
               FROM organization_declared_policy_bundles
               WHERE organization_id = ?
               ORDER BY published_at DESC, bundle_sha256 DESC
               LIMIT ?""",
            (organization_id, cap),
        )
        rows = cur.fetchall()
        columns = [item[0] for item in cur.description] if cur.description else None
    result: list[dict[str, Any]] = []
    for row in rows:
        item = _decode_row(db, row, columns)
        if item:
            result.append(item)
    return result
