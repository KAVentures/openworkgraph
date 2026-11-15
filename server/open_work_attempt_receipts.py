"""Opt-in local, scoped verification journal; never grants action authority.

Only conservative provider-event verdicts are stored. Caller owns consent,
retention and revocation; no provider credentials or raw provider responses.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .open_work_provider_attempt_verification import _valid

_MAX_ROWS = 500


def _scope(tenant: str, actor: str, attempt: str) -> None:
    if not all(_valid(x) and x not in (".", "..") and "/" not in x and "\\" not in x for x in (tenant, actor, attempt)):
        raise ValueError("invalid journal scope")


def _connect(path: str):
    if not isinstance(path, str) or not path or path == ":memory:":
        raise ValueError("explicit durable journal path required")
    target = Path(path).expanduser()
    if target.is_symlink():
        raise ValueError("journal symlink refused")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Never chmod an existing inode: a hard link could change another file's
    # permissions, and an attacker-controlled path must not be opened as SQLite.
    import stat
    try:
        info = target.lstat()
    except FileNotFoundError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, flags, 0o600)
        except FileExistsError:
            pass  # Concurrent creator; validate the resulting inode below.
        else:
            os.close(fd)
        info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or target.is_symlink():
        raise ValueError("unsafe receipt journal path")
    if os.name == "posix" and info.st_mode & 0o077:
        raise ValueError("receipt journal permissions too broad")
    # Do not adopt pre-existing WAL/SHM files from an untrusted filesystem.
    # The receipt journal deliberately uses rollback journaling so revocation
    # and retention do not leave a second persistent SQLite data store.
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise ValueError("unsupported receipt journal sidecar")
    db = sqlite3.connect(str(target), timeout=5)
    db.execute("PRAGMA busy_timeout=5000")
    if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
        db.close()
        raise ValueError("unsupported receipt journal mode")
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
    # Refuse contradictory provider evidence; labels alone are not receipts.
    if set(verdict) - {"status", "reason", "event_id", "provider_operation", "customer_outcome", "checked_at"}:
        raise ValueError("unsupported verification fields")
    if verdict["status"] == "verified_provider_operation" and verdict["provider_operation"] == "unknown":
        raise ValueError("operation verification requires a known operation verdict")
    if verdict["status"] == "verified_provider_event" and verdict["provider_operation"] != "unknown":
        raise ValueError("event verification alone cannot establish operation result")
    if verdict["status"] == "unknown" and verdict.get("event_id") is not None:
        raise ValueError("unknown verification cannot establish event identity")
    if verdict["status"] != "unknown" and not verdict.get("event_id"):
        raise ValueError("verified provider evidence requires an exact event identity")
    event_id = verdict.get("event_id")
    if event_id is not None and not _valid(event_id):
        raise ValueError("invalid event identity")
    reason = verdict.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 160):
        raise ValueError("invalid reason")
    # Validate independently supplied verifier metadata before persistence.
    source_checked = verdict.get("checked_at")
    if source_checked is not None:
        if not isinstance(source_checked, str) or len(source_checked) > 64:
            raise ValueError("invalid source verification timestamp")
        try:
            parsed = datetime.fromisoformat(source_checked.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid source verification timestamp") from None
        if parsed.tzinfo is None:
            raise ValueError("source verification timestamp requires timezone")
        # Journal checked_at is the local append time, not a source freshness claim.
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


def purge_expired_receipts(path: str, *, tenant: str, actor: str,
                           older_than_days: int, now: datetime | None = None) -> int:
    """Explicit actor-scoped retention operation; no implicit background deletion.

    Only receipts strictly older than the UTC cutoff are deleted. This does not
    revoke access: callers must separately enforce consent and grants.
    """
    _scope(tenant, actor, "retention")
    if type(older_than_days) is not int or not 1 <= older_than_days <= 3650:
        raise ValueError("invalid retention window")
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    cutoff = (clock.astimezone(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "DELETE FROM attempt_receipts WHERE tenant=? AND actor=? AND checked_at<?",
            (tenant, actor, cutoff))
        return cursor.rowcount


def purge_tenant(path: str, *, tenant: str) -> int:
    """Revoke all actors' receipts for one disconnected source scope.

    Only a trusted server-side source lifecycle may call this. A browser or
    agent must never supply the tenant; actor-scoped CLI purge remains separate.
    """
    _scope(tenant, "source_revocation", "purge")
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "DELETE FROM attempt_receipts WHERE tenant=?", (tenant,))
        return cursor.rowcount
