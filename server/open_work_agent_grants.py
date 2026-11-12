"""Local installation-scoped, operator-approved, revocable dispatch agent credentials.

Credentials are not business-system action authority. Stored as SHA256 digests,
never plaintext. Every use re-reads SQLite so revocation survives processes.
"""
from __future__ import annotations
import hashlib
import os
import stat
from contextlib import contextmanager
import hmac
import re
import secrets
import sqlite3
import time
from .local_auth import auth_dir

_REF = re.compile(r"^owg:w:[0-9a-f]{32}$")
_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:-]{0,63}$")

class GrantError(ValueError):
    pass

@contextmanager
def _db():
    path = auth_dir() / ".open_work_agent_grants.sqlite3"
    if path.is_symlink():
        raise OSError("unsafe agent grant store path")
    connection = sqlite3.connect(path, timeout=10)
    if os.name == "posix":
        os.chmod(path, 0o600)
    try:
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("""CREATE TABLE IF NOT EXISTS grants (
            id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
            work_item_ref TEXT NOT NULL, agent TEXT NOT NULL,
            expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL)""")
        # Older credentials predate offer-generation binding; leave their marker
        # empty so they fail closed instead of regaining authority on re-offer.
        columns = {row[1] for row in connection.execute("PRAGMA table_info(grants)")}
        if "offered_at" not in columns:
            connection.execute("ALTER TABLE grants ADD COLUMN offered_at TEXT NOT NULL DEFAULT ''")
        if "source_scope" not in columns:
            connection.execute("ALTER TABLE grants ADD COLUMN source_scope TEXT NOT NULL DEFAULT ''")
        if "context_approved" not in columns:
            connection.execute("ALTER TABLE grants ADD COLUMN context_approved INTEGER NOT NULL DEFAULT 0")
        connection.commit()
        yield connection
        connection.commit()
    finally:
        connection.close()

def issue(work_item_ref: str, agent: str, hours: int, *, confirmed: bool, source_scope: str | None = None, context_approved: bool = False) -> dict:
    if confirmed is not True:
        raise GrantError("explicit owner confirmation required")
    if not isinstance(work_item_ref, str) or not _REF.fullmatch(work_item_ref):
        raise GrantError("invalid work item reference")
    if not isinstance(agent, str) or not _AGENT.fullmatch(agent):
        raise GrantError("invalid agent identity label")
    if type(hours) is not int or not 1 <= hours <= 168:
        raise GrantError("hours must be 1-168")
    from .open_work_dispatch import status
    current = status(owner=True)
    if not current["enabled"] or not any(
        item["work_item_ref"] == work_item_ref and item["status"] == "offered"
        for item in current["offers"]
    ):
        raise GrantError("case must be explicitly offered first")
    offered_at = next(item["offered_at"] for item in current["offers"]
                      if item["work_item_ref"] == work_item_ref and item["status"] == "offered")
    if type(context_approved) is not bool:
        raise GrantError("invalid case-context approval")
    if context_approved and source_scope is None:
        raise GrantError("case context requires a connected source")
    if source_scope is not None and not re.fullmatch(r"owg:scope:[0-9a-f]{32}", source_scope):
        raise GrantError("invalid source scope")
    token = secrets.token_urlsafe(48)
    grant_id = secrets.token_hex(16)
    now = int(time.time())
    with _db() as db:
        db.execute("INSERT INTO grants(id,digest,work_item_ref,agent,expires_at,created_at,offered_at,source_scope,context_approved) VALUES (?,?,?,?,?,?,?,?,?)",
                   (grant_id, hashlib.sha256(token.encode()).hexdigest(), work_item_ref,
                    agent, now + hours * 3600, now, offered_at, source_scope or "", int(context_approved)))
    return {"grant_id": grant_id, "token": token, "work_item_ref": work_item_ref,
            "agent": agent, "expires_at": now + hours * 3600, "action_authority": False}

def authenticate(header: str | None, work_item_ref: str | None = None) -> dict | None:
    if not isinstance(header, str) or not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if not 32 <= len(token) <= 256:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _db() as db:
        row = db.execute("SELECT id,work_item_ref,agent,expires_at,revoked,offered_at,source_scope,context_approved FROM grants WHERE digest=?",
                         (digest,)).fetchone()
    if row is None or row[4] or row[3] <= int(time.time()):
        return None
    if work_item_ref is not None and not hmac.compare_digest(row[1], str(work_item_ref)):
        return None
    # A stored grant is not perpetual permission: withdrawing an offer, clearing
    # its review intention, or switching dispatch off must immediately deny use.
    from .open_work_dispatch import status
    current = status(owner=True)
    if not current["enabled"] or not any(
        item["work_item_ref"] == row[1] and item["status"] in ("offered", "claimed")
        and row[5] and hmac.compare_digest(item["offered_at"], row[5])
        for item in current["offers"]
    ):
        return None
    return {"grant_id": row[0], "work_item_ref": row[1], "agent": row[2], "expires_at": row[3],
            "source_scope": row[6] or None, "context_approved": bool(row[7])}

def revoke(grant_id: str) -> bool:
    if not isinstance(grant_id, str) or not re.fullmatch(r"[0-9a-f]{32}", grant_id):
        raise GrantError("invalid grant identifier")
    with _db() as db:
        cursor = db.execute("UPDATE grants SET revoked=1 WHERE id=? AND revoked=0", (grant_id,))
        return cursor.rowcount == 1

def list_grants() -> list[dict]:
    with _db() as db:
        rows = db.execute("SELECT id,work_item_ref,agent,expires_at,revoked FROM grants ORDER BY created_at DESC LIMIT 100").fetchall()
    return [{"grant_id": r[0], "work_item_ref": r[1], "agent": r[2],
             "expires_at": r[3], "active": not r[4] and r[3] > int(time.time())} for r in rows]
