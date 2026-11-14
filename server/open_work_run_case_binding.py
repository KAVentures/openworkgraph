from __future__ import annotations

"""Local, operator-confirmed run/case bindings. No inferred identity or outcome."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Iterator

from .local_auth import auth_dir
from .open_work_case_declarations import CaseDeclarationError
from shared.history_policy import read_policy

_DB = ".open_work_run_case_bindings.sqlite3"
_SCOPE = re.compile(r"^owg:scope:[0-9a-f]{32}$")
_CASE = re.compile(r"^owg:w:[0-9a-f]{32}$")
_RUN = re.compile(r"^owg:run:[0-9a-f]{32}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_MAX_ROWS = 100
_MAX_BYTES = 2 * 1024 * 1024
_TTL = timedelta(days=7)


def _now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CaseDeclarationError("timezone-aware binding clock required")
    return value.astimezone(timezone.utc)


def _retention() -> timedelta:
    try:
        retention = read_policy().get("human_retention", {})
        if retention.get("mode") == "days":
            days = retention.get("days")
            if type(days) is int and days > 0:
                return min(_TTL, timedelta(days=days))
        if retention.get("mode") == "forever":
            return _TTL
    except (ValueError, TypeError, OSError, AttributeError):
        pass
    raise CaseDeclarationError("binding retention policy unavailable")


def _validate(scope: str, case_ref: str, run_ref: str, fingerprint: str, revision: int) -> None:
    if (not isinstance(scope, str) or not _SCOPE.fullmatch(scope)
            or not isinstance(case_ref, str) or not _CASE.fullmatch(case_ref)
            or not isinstance(run_ref, str) or not _RUN.fullmatch(run_ref)
            or not isinstance(fingerprint, str) or not _HEX.fullmatch(fingerprint)
            or type(revision) is not int or not 1 <= revision <= 2147483647):
        raise CaseDeclarationError("invalid scoped run/case selection")


def _actor_key(secret: str, scope: str, actor: str) -> str:
    if (not isinstance(secret, str) or len(secret) < 16
            or not isinstance(actor, str) or not 1 <= len(actor) <= 128
            or any(ord(c) < 33 or ord(c) > 126 for c in actor)):
        raise CaseDeclarationError("invalid actor or local binding key")
    return hmac.new(secret.encode(), ("open-work-binding:v1:" + scope + ":" + actor).encode(),
                    hashlib.sha256).hexdigest()


@contextmanager
def _database(create: bool) -> Iterator[sqlite3.Connection | None]:
    path = auth_dir() / _DB
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            yield None
            return
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
            or info.st_nlink != 1 or info.st_size > _MAX_BYTES):
        raise CaseDeclarationError("unsafe binding ledger")
    conn = sqlite3.connect(str(path), timeout=3)
    try:
        conn.execute("PRAGMA busy_timeout=3000")
        if create:
            conn.execute("""CREATE TABLE IF NOT EXISTS bindings (
                actor_key TEXT NOT NULL, scope TEXT NOT NULL, case_ref TEXT NOT NULL,
                run_ref TEXT NOT NULL, fingerprint TEXT NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                PRIMARY KEY(actor_key, scope, case_ref, run_ref))""")
            conn.commit()
        yield conn
    except sqlite3.Error:
        conn.rollback()
        raise CaseDeclarationError("binding ledger unavailable") from None
    finally:
        conn.close()


def bind_run(*, scope: str, actor: str, secret: str, case_ref: str, run_ref: str,
             fingerprint: str, revision: int, now: datetime | None = None) -> dict:
    """Record an explicitly operator-confirmed association, not proof of run identity.

    Callers MUST re-admit current actor, consent, source and case authorization.
    """
    _validate(scope, case_ref, run_ref, fingerprint, revision)
    actor_key = _actor_key(secret, scope, actor)
    current = _now(now)
    expiry = current + _retention()
    with _database(True) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM bindings WHERE expires_at <= ?", (current.isoformat(),))
        count = db.execute("SELECT COUNT(*) FROM bindings").fetchone()[0]
        existing = db.execute("""SELECT fingerprint, revision FROM bindings
            WHERE actor_key=? AND scope=? AND case_ref=? AND run_ref=?""",
            (actor_key, scope, case_ref, run_ref)).fetchone()
        if existing and existing != (fingerprint, revision):
            raise CaseDeclarationError("existing binding requires explicit revocation")
        if not existing and count >= _MAX_ROWS:
            raise CaseDeclarationError("binding capacity reached")
        if not existing:
            db.execute("INSERT INTO bindings VALUES (?,?,?,?,?,?,?,?)",
                       (actor_key, scope, case_ref, run_ref, fingerprint, revision,
                        current.isoformat(), expiry.isoformat()))
        db.commit()
    return {"case_ref": case_ref, "run_ref": run_ref, "association": "operator_confirmed",
            "run_case_identity_independently_verified": False,
            "customer_outcome_verified": False, "agent_execution_authorized": False}


def get_binding(*, scope: str, actor: str, secret: str, case_ref: str, run_ref: str,
                fingerprint: str, revision: int, now: datetime | None = None) -> dict | None:
    """Fail closed on changed source snapshot, declaration revision or expiry."""
    _validate(scope, case_ref, run_ref, fingerprint, revision)
    key = _actor_key(secret, scope, actor)
    current = _now(now).isoformat()
    _retention()  # Deny access when policy is unavailable/revoked.
    with _database(False) as db:
        if db is None:
            return None
        try:
            row = db.execute("""SELECT fingerprint, revision, expires_at FROM bindings
                WHERE actor_key=? AND scope=? AND case_ref=? AND run_ref=?""",
                (key, scope, case_ref, run_ref)).fetchone()
        except sqlite3.Error:
            raise CaseDeclarationError("binding ledger unavailable") from None
    if not row or row[2] <= current or row[:2] != (fingerprint, revision):
        return None
    return {"case_ref": case_ref, "run_ref": run_ref, "association": "operator_confirmed",
            "run_case_identity_independently_verified": False,
            "customer_outcome_verified": False, "agent_execution_authorized": False}


def revoke_binding(*, scope: str, actor: str, secret: str, case_ref: str, run_ref: str) -> bool:
    """Explicit local revocation; absent ledger never created on revoke."""
    _validate(scope, case_ref, run_ref, "0" * 64, 1)
    key = _actor_key(secret, scope, actor)
    with _database(False) as db:
        if db is None:
            return False
        try:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("""DELETE FROM bindings
                WHERE actor_key=? AND scope=? AND case_ref=? AND run_ref=?""",
                (key, scope, case_ref, run_ref))
            deleted = result.rowcount > 0
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise CaseDeclarationError("binding ledger unavailable") from None
    return deleted


def clear_binding_scope(scope: str) -> int:
    """Erase every actor's bindings for a disconnected/replaced source scope.

    Called only by the trusted local source-change guard, never by an agent or
    a client-supplied scope. Uses one transaction so concurrent readers cannot
    observe a partially cleared source ledger. Missing stores are not created.
    """
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        raise CaseDeclarationError("invalid binding source scope")
    with _database(False) as db:
        if db is None:
            return 0
        try:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("DELETE FROM bindings WHERE scope=?", (scope,))
            removed = result.rowcount
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise CaseDeclarationError("binding ledger unavailable") from None
    return removed



def revoke_case_bindings(*, scope: str, actor: str, secret: str, case_ref: str) -> int:
    """Operator bulk-revoke this actor's associations for one admitted case.

    Caller must independently authenticate the actor and recheck current source,
    case declaration, consent and retention. No run ID is required; a mistaken
    association can be removed even when the imported run is no longer retained.
    """
    _validate(scope, case_ref, "owg:run:" + "0" * 32, "0" * 64, 1)
    key = _actor_key(secret, scope, actor)
    _retention()  # Revocation is permitted only with an admitted policy.
    with _database(False) as db:
        if db is None:
            return 0
        try:
            db.execute("BEGIN IMMEDIATE")
            removed = db.execute(
                "DELETE FROM bindings WHERE actor_key=? AND scope=? AND case_ref=?",
                (key, scope, case_ref),
            ).rowcount
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise CaseDeclarationError("binding ledger unavailable") from None
    return removed


def list_case_bindings(*, scope: str, actor: str, secret: str, case_ref: str,
                       revision: int, now: datetime | None = None) -> dict:
    """Read a bounded, session-owned inventory; no provider or run identity claim.

    Callers MUST independently admit the session, source, declaration and
    retention before and after this read. A changed revision is displayed as
    stale, never silently accepted as an authoritative association.
    """
    _validate(scope, case_ref, "owg:run:" + "0" * 32, "0" * 64, revision)
    key = _actor_key(secret, scope, actor)
    current = _now(now).isoformat()
    _retention()
    with _database(False) as db:
        if db is None:
            rows = []
        else:
            try:
                rows = db.execute("""SELECT run_ref, revision
                    FROM bindings WHERE actor_key=? AND scope=? AND case_ref=?
                    AND expires_at > ? ORDER BY run_ref LIMIT ?""",
                    (key, scope, case_ref, current, _MAX_ROWS + 1)).fetchall()
            except sqlite3.Error:
                raise CaseDeclarationError("binding ledger unavailable") from None
    if len(rows) > _MAX_ROWS:
        raise CaseDeclarationError("binding inventory capacity exceeded")
    return {
        "format": "openworkgraph.local-case-run-bindings.v1",
        "case_ref": case_ref,
        "items": [{"run_ref": ref, "declaration_state":
                   "current_revision" if saved_revision == revision else "stale_revision",
                   "run_identity_independently_verified": False,
                   "customer_outcome_verified": False}
                  for ref, saved_revision in rows],
        "source_read_performed": False,
        "run_snapshots_rechecked": False,
        "agent_execution_authorized": False,
    }


def require_exclusive_run_case_binding(*, scope: str, actor: str, secret: str,
                                       case_ref: str, run_ref: str,
                                       fingerprint: str, revision: int) -> None:
    """Refuse case attribution of run-only receipts when bindings are ambiguous.

    The legacy receipt journal is keyed by run, not case. A run bound to two
    different cases cannot safely have its receipts attributed to either case.
    This is a read guard, not evidence of run identity or provider success.
    """
    _validate(scope, case_ref, run_ref, fingerprint, revision)
    key = _actor_key(secret, scope, actor)
    current = _now(None).isoformat()
    _retention()
    with _database(False) as db:
        if db is None:
            raise CaseDeclarationError("current run/case binding required")
        try:
            rows = db.execute("""SELECT case_ref, fingerprint, revision FROM bindings
                WHERE actor_key=? AND scope=? AND run_ref=? AND expires_at>?
                LIMIT 2""", (key, scope, run_ref, current)).fetchall()
        except sqlite3.Error:
            raise CaseDeclarationError("binding ledger unavailable") from None
    if len(rows) != 1 or rows[0] != (case_ref, fingerprint, revision):
        raise CaseDeclarationError("ambiguous or stale run/case receipt attribution")
