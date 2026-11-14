"""Bounded private operator run-evidence review journal.

An operator's association and evidence review are NOT independently verified
run causality, provider action, or customer outcome.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import uuid

from .local_auth import auth_dir
from .open_work_case_declarations import CaseDeclarationError
from .open_work_run_case_binding import _actor_key, _retention, _validate

_NAME = ".open_work_run_receipts.sqlite3"
_LIMIT = 256
_BYTES = 2 * 1024 * 1024
_ACTIONS = {"reviewed", "disputed", "retracted"}
_HEX = re.compile(r"^[0-9a-f]{64}$")


def _db(create: bool):
    path = auth_dir() / _NAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            return None
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
            or info.st_nlink != 1 or info.st_size > _BYTES
            or (os.name == "posix" and info.st_mode & 0o077)):
        raise CaseDeclarationError("unsafe run receipt journal")
    # SQLite WAL and shared-memory files can outlive a consent revocation or
    # retention purge. This local evidence ledger supports DELETE mode only.
    # Check dangling symlinks as well as existing sidecars before opening.
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise CaseDeclarationError("unsafe run receipt journal sidecar")
    db = sqlite3.connect(str(path), timeout=3)
    try:
        db.execute("PRAGMA busy_timeout=3000")
        if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
            raise CaseDeclarationError("unsupported run receipt journal mode")
    except Exception:
        db.close()
        raise
    if create:
        db.execute("""CREATE TABLE IF NOT EXISTS receipts (
            id TEXT PRIMARY KEY, actor_key TEXT NOT NULL, scope TEXT NOT NULL,
            case_ref TEXT NOT NULL, run_ref TEXT NOT NULL, fingerprint TEXT NOT NULL,
            revision INTEGER NOT NULL, action TEXT NOT NULL, evidence_digest TEXT NOT NULL,
            supersedes TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            mac TEXT NOT NULL)""")
        db.commit()
    return db


def _mac(secret: str, fields: tuple) -> str:
    return hmac.new(secret.encode(), json.dumps(fields, separators=(",", ":")).encode(),
                    hashlib.sha256).hexdigest()


def _check_rows(db, secret, actor_key, scope, case_ref, run_ref, now, cutoff):
    rows = db.execute("""SELECT id, actor_key, scope, case_ref, run_ref, fingerprint,
        revision, action, evidence_digest, supersedes, created_at, expires_at, mac
        FROM receipts WHERE actor_key=? AND scope=? AND case_ref=? AND run_ref=?
        ORDER BY created_at, id LIMIT ?""",
        (actor_key, scope, case_ref, run_ref, _LIMIT + 1)).fetchall()
    if len(rows) > _LIMIT:
        raise CaseDeclarationError("run receipt capacity exceeded")
    for row in rows:
        if not hmac.compare_digest(row[-1], _mac(secret, row[:-1])):
            raise CaseDeclarationError("run receipt integrity check failed")
    return [row for row in rows if row[11] > now and row[10] > cutoff]


def review(*, scope: str, actor: str, secret: str, case_ref: str, run_ref: str,
           fingerprint: str, revision: int, guard=None, now=None):
    _validate(scope, case_ref, run_ref, fingerprint, revision)
    key = _actor_key(secret, scope, actor)
    retention = _retention()
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise CaseDeclarationError("timezone-aware receipt clock required")
    clock = clock.astimezone(timezone.utc)
    current = clock.isoformat()
    cutoff = (clock - retention).isoformat()
    if guard:
        guard()
    db = _db(False)
    if db is None:
        rows = []
    else:
        try:
            rows = _check_rows(db, secret, key, scope, case_ref, run_ref, current, cutoff)
        except sqlite3.Error:
            raise CaseDeclarationError("run receipt journal unavailable") from None
        finally:
            db.close()
    if guard:
        guard()
    current_rows = [r for r in rows if r[5] == fingerprint and r[6] == revision]
    superseded = {r[9] for r in current_rows if r[9]}
    active = [r for r in current_rows if r[0] not in superseded and r[7] != "retracted"]
    return {"format": "openworkgraph.operator-run-receipt-journal.v1",
            "run_ref": run_ref, "case_ref": case_ref,
            "entries": [{"id": r[0], "action": r[7], "evidence_digest": r[8],
                         "supersedes": r[9], "created_at": r[10]} for r in current_rows],
            "active_review_count": len(active),
            "association": "operator_confirmed",
            "provider_run_identity_independently_verified": False,
            "agent_caused_operation_verified": False,
            "customer_outcome_verified": False, "agent_execution_authorized": False}


def append(*, scope: str, actor: str, secret: str, case_ref: str, run_ref: str,
           fingerprint: str, revision: int, action: str, evidence_digest: str,
           supersedes: str | None = None, guard=None, now=None):
    _validate(scope, case_ref, run_ref, fingerprint, revision)
    if (action not in _ACTIONS or not isinstance(evidence_digest, str)
            or not _HEX.fullmatch(evidence_digest)
            or (supersedes is not None and
                (not isinstance(supersedes, str) or not re.fullmatch(r"[0-9a-f]{32}", supersedes)))):
        raise CaseDeclarationError("invalid run receipt selection")
    if action == "retracted" and not supersedes:
        raise CaseDeclarationError("retraction requires an existing receipt")
    key = _actor_key(secret, scope, actor)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise CaseDeclarationError("timezone-aware receipt clock required")
    current = current.astimezone(timezone.utc)
    expiry = current + _retention()
    if guard:
        guard()
    db = _db(True)
    try:
        db.execute("BEGIN IMMEDIATE")
        if guard:
            guard()
        rows = _check_rows(db, secret, key, scope, case_ref, run_ref, current.isoformat(),
                           (current - _retention()).isoformat())
        if len(rows) >= _LIMIT:
            raise CaseDeclarationError("run receipt capacity reached")
        if supersedes:
            matches = [r for r in rows if r[0] == supersedes and
                       r[5] == fingerprint and r[6] == revision]
            if not matches or any(r[9] == supersedes for r in rows):
                raise CaseDeclarationError("receipt correction target missing or already superseded")
        entry = (uuid.uuid4().hex, key, scope, case_ref, run_ref, fingerprint,
                 revision, action, evidence_digest, supersedes,
                 current.isoformat(), expiry.isoformat())
        db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (*entry, _mac(secret, entry)))
        if guard:
            guard()
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise CaseDeclarationError("run receipt journal unavailable") from None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return review(scope=scope, actor=actor, secret=secret, case_ref=case_ref,
                  run_ref=run_ref, fingerprint=fingerprint, revision=revision, guard=guard)


def clear_scope(scope: str):
    if not isinstance(scope, str) or not re.fullmatch(r"owg:scope:[0-9a-f]{32}", scope):
        raise CaseDeclarationError("invalid source scope")
    db = _db(False)
    if db is None:
        return 0
    try:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("DELETE FROM receipts WHERE scope=?", (scope,)).rowcount
        db.commit()
        return count
    finally:
        db.close()
