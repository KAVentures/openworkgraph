from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.time_utils import normalize_timestamp
from .auth import issue_token, token_hash
from .db import GatewayDB


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollment_grants (
  grant_id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_enrollment_grants_org ON enrollment_grants(organization_id, used_at);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollment_grants (
  grant_id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_enrollment_grants_org ON enrollment_grants(organization_id, used_at);
"""


def init_enrollment_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        schema = POSTGRES_SCHEMA if db.is_postgres else SQLITE_SCHEMA
        if db.is_postgres:
            for statement in [x.strip() for x in schema.split(";") if x.strip()]:
                conn.execute(statement)
        else:
            conn.executescript(schema)


def create_enrollment_grant(
    db: GatewayDB,
    *,
    organization_id: str,
    actor_id: str = "",
    expires_minutes: int = 30,
) -> dict[str, Any]:
    org = str(organization_id or "").strip()
    actor = str(actor_id or "").strip()
    if not org:
        raise ValueError("organization_id is required")
    ttl = max(1, min(int(expires_minutes), 24 * 60))
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ttl)
    token = issue_token("owg_enroll_once")
    grant_id = token.split(".", 1)[0] + "_" + token_hash(token)[:16]
    with db.connect() as conn:
        db._execute(
            conn,
            """INSERT INTO enrollment_grants(
                grant_id, token_hash, organization_id, actor_id, created_at, expires_at, used_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)""",
            (
                grant_id,
                token_hash(token),
                org,
                actor,
                normalize_timestamp(now.isoformat()),
                normalize_timestamp(expires.isoformat()),
            ),
        )
    return {
        "grant_id": grant_id,
        "token": token,
        "organization_id": org,
        "actor_id": actor,
        "expires_at": normalize_timestamp(expires.isoformat()),
        "single_use": True,
    }


def consume_enrollment_grant(db: GatewayDB, token: str) -> dict[str, str] | None:
    digest = token_hash(str(token or ""))
    if not token:
        return None
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT grant_id, organization_id, actor_id, expires_at, used_at
               FROM enrollment_grants WHERE token_hash = ?""",
            (digest,),
        )
        row = cur.fetchone()
        columns = [d[0] for d in cur.description] if cur.description else None
        data = db._row(row, columns)
        if not data or data.get("used_at"):
            return None
        try:
            expires_at = normalize_timestamp(str(data.get("expires_at") or ""))
        except ValueError:
            return None
        now = normalize_timestamp(datetime.now(timezone.utc).isoformat())
        if expires_at < now:
            return None
        used_at = now
        update = db._execute(
            conn,
            "UPDATE enrollment_grants SET used_at = ? WHERE grant_id = ? AND used_at IS NULL",
            (used_at, str(data["grant_id"])),
        )
        if not update.rowcount:
            return None
        return {
            "grant_id": str(data["grant_id"]),
            "organization_id": str(data["organization_id"]),
            "actor_id": str(data.get("actor_id") or ""),
        }


def active_device_exists(db: GatewayDB, *, organization_id: str, device_id: str) -> bool:
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT 1 FROM access_tokens
               WHERE organization_id = ? AND device_id = ? AND token_type = 'device'
                 AND revoked_at IS NULL LIMIT 1""",
            (organization_id, device_id),
        )
        return cur.fetchone() is not None
