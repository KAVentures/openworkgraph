"""Personal-account opt-in for searching raw private title/target captions.

This preference is separate from cloud work-text capture consent. Merely
asking the MCP tool for include_private_matches is never sufficient. The
setting can only be changed via a separate OAuth-authenticated account
settings endpoint; the ten read-only MCP tools cannot mutate it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

CONSENT_VERSION = "private-title-search-v1"
SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS private_title_search_preferences (
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (organization_id, actor_id)
);
"""


def _personal(organization_id: str, actor_id: str) -> bool:
    return bool(organization_id.startswith("oauth-sub:") and organization_id == actor_id)


def init_schema(db: Any) -> None:
    with db.connect() as conn:
        for statement in SQL_SCHEMA.split(";"):
            if statement.strip():
                db._execute(conn, statement.strip())


def is_enabled(db: Any, *, organization_id: str, actor_id: str) -> bool:
    if not _personal(organization_id, actor_id):
        return False
    with db.connect() as conn:
        cur = db._execute(
            conn,
            "SELECT enabled FROM private_title_search_preferences "
            "WHERE organization_id=? AND actor_id=?",
            (organization_id, actor_id),
        )
        row = cur.fetchone()
    return bool(row and row[0] == 1)


def set_enabled(db: Any, *, organization_id: str, actor_id: str,
                enabled: bool) -> dict[str, Any]:
    if not _personal(organization_id, actor_id):
        raise PermissionError("private-title search is only available to personal OAuth accounts")
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        db._execute(
            conn,
            "INSERT INTO private_title_search_preferences "
            "(organization_id, actor_id, enabled, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(organization_id, actor_id) DO UPDATE SET "
            "enabled=excluded.enabled, updated_at=excluded.updated_at",
            (organization_id, actor_id, 1 if enabled else 0, now),
        )
    return {"enabled": bool(enabled), "consent_version": CONSENT_VERSION,
            "requires_user_opt_in": True,
            "notice": "Matching original title and target text can reveal whether a private name appears in your synced activity."}
