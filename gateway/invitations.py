from __future__ import annotations

"""Reusable, revocable organization enrollment invitations.

The final seat claim and device credential creation happen in one database
transaction. Invalid identities, duplicate devices, expired/revoked links and
races for the last seat therefore never consume a seat.
"""

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from shared.time_utils import normalize_timestamp

from .auth import DEVICE_SCOPES, issue_token, token_hash
from .db import GatewayDB


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollment_invitations (
  invitation_id TEXT PRIMARY KEY,
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
CREATE INDEX IF NOT EXISTS idx_enrollment_invitations_org
  ON enrollment_invitations(organization_id, created_at);
"""

POSTGRES_SCHEMA = SQLITE_SCHEMA


class InvitationError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return normalize_timestamp(datetime.now(timezone.utc).isoformat())


def _row(db: GatewayDB, cur: Any) -> dict[str, Any]:
    value = cur.fetchone()
    columns = [item[0] for item in cur.description] if cur.description else None
    return db._row(value, columns)


def init_invitation_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        if db.is_postgres:
            for statement in [part.strip() for part in POSTGRES_SCHEMA.split(";") if part.strip()]:
                conn.execute(statement)
        else:
            conn.executescript(SQLITE_SCHEMA)


def create_invitation(
    db: GatewayDB,
    *,
    organization_id: str,
    organization_name: str = "",
    label: str = "",
    max_uses: int = 50,
    expires_days: int = 14,
) -> dict[str, Any]:
    org = str(organization_id or "").strip()
    name = str(organization_name or "").strip()[:120] or org
    if not org or len(org) > 512:
        raise ValueError("organization_id must be 1-512 characters")
    uses = max(1, min(int(max_uses), 10_000))
    days = max(1, min(int(expires_days), 365))
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=days)
    token = issue_token("owg_enroll_link")
    invitation_id = "invite_" + uuid.uuid4().hex
    with db.connect() as conn:
        db._execute(
            conn,
            """INSERT INTO enrollment_invitations(
                invitation_id, token_hash, organization_id, organization_name, label,
                created_at, expires_at, max_uses, use_count, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)""",
            (
                invitation_id,
                token_hash(token),
                org,
                name,
                str(label or "").strip()[:120],
                normalize_timestamp(now.isoformat()),
                normalize_timestamp(expires.isoformat()),
                uses,
            ),
        )
    return {
        "invitation_id": invitation_id,
        "token": token,
        "organization_id": org,
        "organization_name": name,
        "label": str(label or "").strip()[:120],
        "max_uses": uses,
        "use_count": 0,
        "expires_at": normalize_timestamp(expires.isoformat()),
        "revoked": False,
    }


def _status(item: dict[str, Any]) -> str:
    if item.get("revoked_at"):
        return "revoked"
    try:
        if normalize_timestamp(str(item.get("expires_at") or "")) < _now():
            return "expired"
    except ValueError:
        return "invalid"
    if int(item.get("use_count") or 0) >= int(item.get("max_uses") or 0):
        return "used_up"
    return "active"


def list_invitations(db: GatewayDB, *, organization_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT invitation_id, organization_id, organization_name, label, created_at,
                      expires_at, max_uses, use_count, revoked_at
               FROM enrollment_invitations WHERE organization_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (organization_id, max(1, min(int(limit), 1000))),
        )
        columns = [item[0] for item in cur.description] if cur.description else None
        rows = [db._row(row, columns) for row in cur.fetchall()]
    for item in rows:
        item["status"] = _status(item)
        item["seats_left"] = max(0, int(item.get("max_uses") or 0) - int(item.get("use_count") or 0))
    return rows


def revoke_invitation(db: GatewayDB, *, invitation_id: str) -> dict[str, Any] | None:
    now = _now()
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """UPDATE enrollment_invitations SET revoked_at = ?
               WHERE invitation_id = ? AND revoked_at IS NULL""",
            (now, invitation_id),
        )
        if not cur.rowcount:
            lookup = db._execute(
                conn,
                "SELECT organization_id, revoked_at FROM enrollment_invitations WHERE invitation_id = ?",
                (invitation_id,),
            )
            item = _row(db, lookup)
            if not item:
                return None
            return {"invitation_id": invitation_id, "organization_id": str(item["organization_id"]), "revoked": True}
        lookup = db._execute(
            conn,
            "SELECT organization_id FROM enrollment_invitations WHERE invitation_id = ?",
            (invitation_id,),
        )
        item = _row(db, lookup)
    return {"invitation_id": invitation_id, "organization_id": str(item.get("organization_id") or ""), "revoked": True}


def preview_invitation(db: GatewayDB, token: str) -> dict[str, Any] | None:
    if not token:
        return None
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT invitation_id, organization_id, organization_name, label, expires_at,
                      max_uses, use_count, revoked_at
               FROM enrollment_invitations WHERE token_hash = ?""",
            (token_hash(token),),
        )
        item = _row(db, cur)
    if not item or _status(item) != "active":
        return None
    item["seats_left"] = max(0, int(item["max_uses"]) - int(item["use_count"]))
    item["status"] = "active"
    return item


def enroll_with_invitation(
    db: GatewayDB,
    *,
    token: str,
    actor_id: str,
    device_id: str,
) -> dict[str, Any]:
    actor = str(actor_id or "").strip()
    device = str(device_id or "").strip()
    if not actor or len(actor) > 512:
        raise InvitationError("actor_id must be 1-512 characters")
    if not device or len(device) > 512:
        raise InvitationError("device_id must be 1-512 characters")
    digest = token_hash(str(token or ""))
    if not token:
        raise InvitationError("valid organization invitation required", status_code=401)

    now = _now()
    device_token = issue_token("owg_device")
    token_id = "device_" + uuid.uuid4().hex

    # One transaction owns validation, capacity claim and credential insertion.
    # Any exception before commit rolls all of it back.
    with db.connect() as conn:
        cur = db._execute(
            conn,
            """SELECT invitation_id, organization_id, organization_name, expires_at,
                      max_uses, use_count, revoked_at
               FROM enrollment_invitations WHERE token_hash = ?""",
            (digest,),
        )
        invitation = _row(db, cur)
        if not invitation or invitation.get("revoked_at"):
            raise InvitationError("this organization invitation is invalid or revoked", status_code=401)
        try:
            expires_at = normalize_timestamp(str(invitation.get("expires_at") or ""))
        except ValueError as exc:
            raise InvitationError("this organization invitation is invalid", status_code=401) from exc
        if expires_at < now:
            raise InvitationError("this organization invitation has expired", status_code=401)
        if int(invitation.get("use_count") or 0) >= int(invitation.get("max_uses") or 0):
            raise InvitationError("this organization invitation has no seats left", status_code=401)

        org = str(invitation.get("organization_id") or "")
        duplicate = db._execute(
            conn,
            """SELECT 1 FROM access_tokens
               WHERE organization_id = ? AND device_id = ? AND token_type = 'device'
                 AND revoked_at IS NULL LIMIT 1""",
            (org, device),
        ).fetchone()
        if duplicate is not None:
            raise InvitationError("this computer is already enrolled; revoke or rotate it explicitly", status_code=409)

        claim = db._execute(
            conn,
            """UPDATE enrollment_invitations
               SET use_count = use_count + 1
               WHERE invitation_id = ? AND revoked_at IS NULL
                 AND use_count < max_uses AND expires_at >= ?""",
            (str(invitation["invitation_id"]), now),
        )
        if not claim.rowcount:
            raise InvitationError("this organization invitation has no seats left", status_code=401)

        db._execute(
            conn,
            """INSERT INTO access_tokens(
                token_id, token_hash, token_type, organization_id, actor_id, device_id,
                scopes_json, created_at, revoked_at
            ) VALUES (?, ?, 'device', ?, ?, ?, ?, ?, NULL)""",
            (
                token_id,
                token_hash(device_token),
                org,
                actor,
                device,
                json.dumps(sorted(DEVICE_SCOPES)),
                now,
            ),
        )

    return {
        "token": device_token,
        "token_id": token_id,
        "organization_id": str(invitation["organization_id"]),
        "organization_name": str(invitation.get("organization_name") or invitation["organization_id"]),
        "actor_id": actor,
        "device_id": device,
        "scopes": sorted(DEVICE_SCOPES),
        "invitation_id": str(invitation["invitation_id"]),
        "enrollment_mode": "reusable_invitation",
    }


__all__ = [
    "InvitationError", "init_invitation_schema", "create_invitation", "list_invitations",
    "revoke_invitation", "preview_invitation", "enroll_with_invitation",
]
