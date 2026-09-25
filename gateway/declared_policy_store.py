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
  policy_revision INTEGER NOT NULL,
  bundle_sha256 TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  key_id TEXT NOT NULL,
  bundle_json TEXT NOT NULL,
  published_at TEXT NOT NULL,
  PRIMARY KEY (organization_id, policy_revision)
);
CREATE INDEX IF NOT EXISTS idx_gateway_declared_policy_latest
  ON organization_declared_policy_bundles(organization_id, policy_revision DESC);
"""

_POSTGRES_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS organization_declared_policy_bundles (
      organization_id TEXT NOT NULL,
      policy_revision BIGINT NOT NULL,
      bundle_sha256 TEXT NOT NULL,
      manifest_sha256 TEXT NOT NULL,
      key_id TEXT NOT NULL,
      bundle_json TEXT NOT NULL,
      published_at TEXT NOT NULL,
      PRIMARY KEY (organization_id, policy_revision)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_gateway_declared_policy_latest
      ON organization_declared_policy_bundles(organization_id, policy_revision DESC)""",
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
        "bundle": bundle,
    }


def latest_declared_policy_bundle(db: GatewayDB, organization_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT organization_id, policy_revision, bundle_sha256, manifest_sha256, key_id, bundle_json, published_at
               FROM organization_declared_policy_bundles
               WHERE organization_id = ?
               ORDER BY policy_revision DESC
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
    """Store an envelope after structural validation; endpoints verify authenticity.

    The Gateway intentionally does not possess the private signing key. Publication
    therefore establishes distribution state, not authenticity. Endpoint signature
    verification is the authority boundary.
    """
    try:
        inspected = inspect_policy_bundle(bundle)
    except PolicyBundleError:
        raise
    if inspected["organization_id"] != organization_id:
        raise PolicyBundleError("policy bundle belongs to another organization")

    normalized = inspected["bundle"]
    revision = int(inspected["policy_revision"])
    bundle_sha = str(inspected["bundle_sha256"])
    manifest_sha = str(inspected["manifest_sha256"])
    key_id = str(inspected["key_id"])
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    latest = latest_declared_policy_bundle(db, organization_id)
    if latest is not None:
        current_revision = int(latest["policy_revision"])
        if revision < current_revision:
            raise PolicyBundleError("policy revision rollback rejected")
        if revision == current_revision:
            if bundle_sha != latest["bundle_sha256"]:
                raise PolicyBundleError("policy revision already exists with different signed content")
            return {**latest, "idempotent": True, "signature_verified_by_gateway": False}

    published_at = _now()
    try:
        with db.connect() as conn:
            db._execute(
                conn,
                """INSERT INTO organization_declared_policy_bundles(
                     organization_id, policy_revision, bundle_sha256, manifest_sha256, key_id, bundle_json, published_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (organization_id, revision, bundle_sha, manifest_sha, key_id, payload, published_at),
            )
    except Exception:
        # A concurrent writer may have inserted the same revision. Re-read and
        # accept only byte-identical idempotence; never hide revision equivocation.
        current = latest_declared_policy_bundle(db, organization_id)
        if current and int(current["policy_revision"]) == revision and current["bundle_sha256"] == bundle_sha:
            return {**current, "idempotent": True, "signature_verified_by_gateway": False}
        raise

    return {
        "organization_id": organization_id,
        "policy_revision": revision,
        "bundle_sha256": bundle_sha,
        "manifest_sha256": manifest_sha,
        "key_id": key_id,
        "published_at": published_at,
        "bundle": normalized,
        "idempotent": False,
        "signature_verified_by_gateway": False,
    }


def declared_policy_history(db: GatewayDB, organization_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    cap = max(1, min(int(limit), 200))
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT organization_id, policy_revision, bundle_sha256, manifest_sha256, key_id, bundle_json, published_at
               FROM organization_declared_policy_bundles
               WHERE organization_id = ?
               ORDER BY policy_revision DESC
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
