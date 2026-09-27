from __future__ import annotations

"""Reusable organization enrollment links with transactional seat accounting.

A seat is consumed only in the same database transaction that creates the
corresponding device credential. Invalid identity, organization mismatch,
duplicate device, expired/revoked links, token insertion failure, or a lost
concurrent race therefore cannot burn an invitation seat.
"""

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from shared.time_utils import normalize_timestamp
from .auth import DEVICE_SCOPES, issue_token, token_hash
from .db import GatewayDB


LINKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollment_links (
  grant_id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL,
  organization_name TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  max_uses INTEGER NOT NULL,
  use_count INTEGER NOT NULL DEFAULT 0,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_enrollment_links_org ON enrollment_links(organization_id, revoked_at);
"""

MAX_LINK_USES = 10_000
MAX_LINK_DAYS = 90


class EnrollmentLinkError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def init_enrollment_links_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        for statement in [x.strip() for x in LINKS_SCHEMA.split(";") if x.strip()]:
            conn.execute(statement)


def create_enrollment_link(
    db: GatewayDB,
    *,
    organization_id: str,
    organization_name: str = "",
    max_uses: int = 50,
    expires_days: int = 14,
    label: str = "",
) -> dict[str, Any]:
    org = str(organization_id or "").strip()
    if not org or len(org) > 512:
        raise ValueError("organization_id is required (max 512 characters)")
    display = str(organization_name or "").strip()[:120] or org
    uses = max(1, min(int(max_uses), MAX_LINK_USES))
    days = max(1, min(int(expires_days), MAX_LINK_DAYS))
    clean_label = str(label or "").strip()[:120]
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=days)
    token = issue_token("owg_enroll_link")
    grant_id = "link_" + token_hash(token)[:20]
    with db.connect() as conn:
        db._execute(
            conn,
            """INSERT INTO enrollment_links(
                grant_id, token_hash, organization_id, organization_name, label,
                created_at, expires_at, max_uses, use_count, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)""",
            (
                grant_id,
                token_hash(token),
                org,
                display,
                clean_label,
                normalize_timestamp(now.isoformat()),
                normalize_timestamp(expires.isoformat()),
                uses,
            ),
        )
    return {
        "grant_id": grant_id,
        "token": token,
        "organization_id": org,
        "organization_name": display,
        "label": clean_label,
        "expires_at": normalize_timestamp(expires.isoformat()),
        "max_uses": uses,
        "use_count": 0,
        "single_use": False,
    }


def _link_row(db: GatewayDB, conn: Any, token: str, *, lock: bool = False) -> dict[str, Any]:
    sql = """SELECT grant_id, organization_id, organization_name, label, expires_at,
                    max_uses, use_count, revoked_at
             FROM enrollment_links WHERE token_hash = ?"""
    if lock and db.is_postgres:
        sql += " FOR UPDATE"
    cur = db._execute(conn, sql, (token_hash(str(token)),))
    row = cur.fetchone()
    columns = [d[0] for d in cur.description] if cur.description else None
    return db._row(row, columns)


def peek_enrollment_link(db: GatewayDB, token: str) -> dict[str, Any] | None:
    """Validate a link without consuming a seat."""
    if not token:
        return None
    now = normalize_timestamp(datetime.now(timezone.utc).isoformat())
    with db.connect() as conn:
        data = _link_row(db, conn, token)
    if not data or data.get("revoked_at"):
        return None
    try:
        if normalize_timestamp(str(data.get("expires_at") or "")) < now:
            return None
    except ValueError:
        return None
    left = int(data.get("max_uses") or 0) - int(data.get("use_count") or 0)
    if left <= 0:
        return None
    return {
        "grant_id": str(data["grant_id"]),
        "organization_id": str(data["organization_id"]),
        "organization_name": str(data.get("organization_name") or data["organization_id"]),
        "label": str(data.get("label") or ""),
        "expires_at": data.get("expires_at"),
        "seats_left": left,
    }


def enroll_device_with_link(
    db: GatewayDB,
    *,
    enrollment_token: str,
    requested_organization_id: str,
    actor_id: str,
    device_id: str,
) -> dict[str, Any]:
    """Atomically consume one seat and create one device credential."""
    presented = str(enrollment_token or "").strip()
    actor = str(actor_id or "").strip()
    device = str(device_id or "").strip()
    requested_org = str(requested_organization_id or "").strip()
    if not presented:
        raise EnrollmentLinkError("valid OpenWorkGraph organization join code required", status_code=401)
    if not actor:
        raise EnrollmentLinkError("actor_id is required when joining with an organization link", status_code=400)
    if not device:
        raise EnrollmentLinkError("device_id is required", status_code=400)
    if max(len(actor), len(device), len(requested_org)) > 512:
        raise EnrollmentLinkError("organization/actor/device identifiers must be at most 512 characters", status_code=400)

    now = normalize_timestamp(datetime.now(timezone.utc).isoformat())
    device_token = issue_token("owg_device")
    token_id = f"device_{uuid.uuid4().hex}"

    with db.connect() as conn:
        data = _link_row(db, conn, presented, lock=True)
        if not data or data.get("revoked_at"):
            raise EnrollmentLinkError(
                "this join code is invalid, expired, revoked or used up; ask your IT admin for a new one",
                status_code=401,
            )
        try:
            expires = normalize_timestamp(str(data.get("expires_at") or ""))
        except ValueError as exc:
            raise EnrollmentLinkError("this join code is invalid", status_code=401) from exc
        if expires < now or int(data.get("use_count") or 0) >= int(data.get("max_uses") or 0):
            raise EnrollmentLinkError(
                "this join code is invalid, expired, revoked or used up; ask your IT admin for a new one",
                status_code=401,
            )

        organization_id = str(data.get("organization_id") or "").strip()
        if requested_org and requested_org != organization_id:
            raise EnrollmentLinkError("enrollment link is bound to another organization", status_code=403)

        cur = db._execute(
            conn,
            """SELECT 1 FROM access_tokens
               WHERE organization_id = ? AND device_id = ? AND token_type = 'device'
                 AND revoked_at IS NULL LIMIT 1""",
            (organization_id, device),
        )
        if cur.fetchone() is not None:
            raise EnrollmentLinkError(
                "device_id is already enrolled; revoke or rotate it explicitly",
                status_code=409,
            )

        # The NOT EXISTS guard is repeated in the seat reservation so concurrent
        # requests using the same invitation cannot both consume seats for the
        # same device after racing past the read above.
        update = db._execute(
            conn,
            """UPDATE enrollment_links SET use_count = use_count + 1
               WHERE grant_id = ? AND revoked_at IS NULL AND expires_at >= ?
                 AND use_count < max_uses
                 AND NOT EXISTS (
                   SELECT 1 FROM access_tokens
                   WHERE organization_id = ? AND device_id = ? AND token_type = 'device'
                     AND revoked_at IS NULL
                 )""",
            (str(data["grant_id"]), now, organization_id, device),
        )
        if not update.rowcount:
            cur = db._execute(
                conn,
                """SELECT 1 FROM access_tokens
                   WHERE organization_id = ? AND device_id = ? AND token_type = 'device'
                     AND revoked_at IS NULL LIMIT 1""",
                (organization_id, device),
            )
            if cur.fetchone() is not None:
                raise EnrollmentLinkError(
                    "device_id is already enrolled; revoke or rotate it explicitly",
                    status_code=409,
                )
            raise EnrollmentLinkError(
                "this join code is invalid, expired, revoked or used up; ask your IT admin for a new one",
                status_code=401,
            )

        # Same transaction as the seat increment. Any insertion error rolls the
        # seat reservation back when the connection closes without commit.
        db._execute(
            conn,
            """INSERT INTO access_tokens(
                 token_id, token_hash, token_type, organization_id, actor_id,
                 device_id, scopes_json, created_at, revoked_at
               ) VALUES (?, ?, 'device', ?, ?, ?, ?, ?, NULL)""",
            (
                token_id,
                token_hash(device_token),
                organization_id,
                actor,
                device,
                json.dumps(sorted(DEVICE_SCOPES)),
                now,
            ),
        )

    return {
        "token": device_token,
        "token_id": token_id,
        "organization_id": organization_id,
        "organization_name": str(data.get("organization_name") or organization_id),
        "actor_id": actor,
        "device_id": device,
        "grant_id": str(data["grant_id"]),
        "scopes": sorted(DEVICE_SCOPES),
        "enrollment_mode": "reusable_organization_link",
        "note": "The device token is shown once. Store it in the endpoint credential store/file.",
    }


def list_enrollment_links(db: GatewayDB, *, organization_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT grant_id, organization_id, organization_name, label, created_at,
                      expires_at, max_uses, use_count, revoked_at
               FROM enrollment_links WHERE organization_id = ? ORDER BY created_at DESC""",
            (organization_id,),
        )
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else None
    now = normalize_timestamp(datetime.now(timezone.utc).isoformat())
    items: list[dict[str, Any]] = []
    for row in rows:
        data = db._row(row, columns) or {}
        try:
            expired = normalize_timestamp(str(data.get("expires_at") or "")) < now
        except ValueError:
            expired = True
        exhausted = int(data.get("use_count") or 0) >= int(data.get("max_uses") or 0)
        data["status"] = "revoked" if data.get("revoked_at") else "expired" if expired else "used_up" if exhausted else "active"
        items.append(data)
    return items


def revoke_enrollment_link(db: GatewayDB, *, grant_id: str) -> dict[str, Any] | None:
    now = normalize_timestamp(datetime.now(timezone.utc).isoformat())
    with db.connect() as conn:
        cur = db._execute(conn, "SELECT organization_id, revoked_at FROM enrollment_links WHERE grant_id = ?", (grant_id,))
        row = cur.fetchone()
        columns = [d[0] for d in cur.description] if cur.description else None
        data = db._row(row, columns)
        if not data:
            return None
        if not data.get("revoked_at"):
            db._execute(conn, "UPDATE enrollment_links SET revoked_at = ? WHERE grant_id = ?", (now, grant_id))
    return {
        "grant_id": grant_id,
        "organization_id": str(data["organization_id"]),
        "revoked_at": data.get("revoked_at") or now,
    }


def device_activity(db: GatewayDB, *, organization_id: str) -> dict[str, dict[str, Any]]:
    """Last evidence time and event count per device (for fleet health)."""
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT device_id, MAX(observed_at) AS last_evidence_at, COUNT(*) AS event_count
               FROM evidence_events WHERE organization_id = ? GROUP BY device_id""",
            (organization_id,),
        )
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else None
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = db._row(row, columns) or {}
        out[str(data.get("device_id") or "")] = {
            "last_evidence_at": data.get("last_evidence_at"),
            "event_count": int(data.get("event_count") or 0),
        }
    return out


__all__ = [
    "EnrollmentLinkError",
    "create_enrollment_link",
    "device_activity",
    "enroll_device_with_link",
    "init_enrollment_links_schema",
    "list_enrollment_links",
    "peek_enrollment_link",
    "revoke_enrollment_link",
]
