"""Opt-in local, scoped verification journal; never grants action authority.

Only conservative provider-event verdicts are stored. Caller owns consent,
retention and revocation; no provider credentials or raw provider responses.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .open_work_provider_attempt_verification import _valid

_MAX_ROWS = 500


def _scope(tenant: str, actor: str, attempt: str) -> None:
    if not all(_valid(x) for x in (tenant, actor, attempt)):
        raise ValueError("invalid journal scope")


def _connect(path: str):
    if not isinstance(path, str) or not path or path == ":memory:":
        raise ValueError("explicit durable journal path required")
    target = Path(path).expanduser()
    if target.is_symlink():
        raise ValueError("journal symlink refused")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(str(target), timeout=5)
    if os.name == "posix":
        os.chmod(target, 0o600)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("""CREATE TABLE IF NOT EXISTS attempt_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant TEXT NOT NULL, actor TEXT NOT NULL, attempt TEXT NOT NULL,
        checked_at TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
        event_id TEXT, provider_operation TEXT NOT NULL,
        customer_outcome TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS attempt_receipts_scope ON attempt_receipts(tenant,actor,attempt,id)")
    return db


def append_receipt(path: str, *, tenant: str, actor: str, attempt: str,
                   verdict: dict) -> int:
    """Append a recheck, never replace a prior verdict or infer completion."""
    _scope(tenant, actor, attempt)
    if not isinstance(verdict, dict) or verdict.get("status") not in (
            "unknown", "verified_provider_event", "verified_provider_operation"):
        raise ValueError("invalid verification verdict")
    if verdict.get("customer_outcome") != "unknown":
        raise ValueError("customer outcome is not independently established")
    if verdict.get("provider_operation") not in ("unknown", "succeeded", "failed"):
        raise ValueError("invalid provider operation")
    if verdict["status"] == "unknown" and verdict["provider_operation"] != "unknown":
        raise ValueError("unknown verdict cannot establish operation")
    event_id = verdict.get("event_id")
    if event_id is not None and not _valid(event_id):
        raise ValueError("invalid event identity")
    reason = verdict.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 160):
        raise ValueError("invalid reason")
    checked = datetime.now(timezone.utc).isoformat()
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT COUNT(*) FROM attempt_receipts WHERE tenant=? AND actor=?",
                           (tenant, actor)).fetchone()[0]
        if count >= _MAX_ROWS:
            raise ValueError("journal capacity reached; purge expired entries")
        cursor = db.execute("""INSERT INTO attempt_receipts
            (tenant,actor,attempt,checked_at,status,reason,event_id,provider_operation,customer_outcome)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (tenant, actor, attempt, checked, verdict["status"], reason, event_id,
             verdict["provider_operation"], "unknown"))
        return cursor.lastrowid


def list_receipts(path: str, *, tenant: str, actor: str, attempt: str) -> list[dict]:
    _scope(tenant, actor, attempt)
    with _connect(path) as db:
        rows = db.execute("""SELECT id,checked_at,status,reason,event_id,provider_operation,customer_outcome
            FROM attempt_receipts WHERE tenant=? AND actor=? AND attempt=?
            ORDER BY id DESC LIMIT 100""", (tenant, actor, attempt)).fetchall()
    keys = ("id", "checked_at", "status", "reason", "event_id", "provider_operation", "customer_outcome")
    return [dict(zip(keys, row)) for row in rows]


def purge_scope(path: str, *, tenant: str, actor: str) -> int:
    """Caller invokes on consent withdrawal, connector revocation or deletion."""
    _scope(tenant, actor, "purge")
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute("DELETE FROM attempt_receipts WHERE tenant=? AND actor=?",
                            (tenant, actor))
        return cursor.rowcount
