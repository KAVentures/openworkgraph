from __future__ import annotations

"""Opt-in local declarations for already reviewed Open Work cases.

This is a bounded owner-declared fact ledger, NOT a source-status cache,
provider credential store, case discovery engine, or agent permission grant.
There are no routes, background jobs, source reads, or default writers.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Iterator
from urllib.parse import quote

from .local_auth import auth_dir
from .open_work_reviews import list_review_intentions
from shared.history_policy import read_policy

_REF = re.compile(r"^owg:w:[0-9a-f]{32}$")
_SCOPE = re.compile(r"^owg:scope:[0-9a-f]{32}$")
_GOALS = frozenset({"review_source_state", "assess_declared_policy", "verify_operation"})
_EXPECTED = frozenset({"human_review_recorded", "source_state_rechecked", "operation_receipt_reviewed"})
_FILENAME = ".open_work_case_declarations.sqlite3"
_MAX_ITEMS = 50
_MAX_DEPENDENCIES = 8
_TTL_DAYS = 7
_MAX_REVISION = 2_147_483_647


class CaseDeclarationError(ValueError):
    """Invalid, stale, or unauthorized owner declaration (never provider proof)."""


def _path() -> Path:
    return auth_dir() / _FILENAME


def _current(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CaseDeclarationError("a timezone-aware timestamp is required")
    return value.astimezone(timezone.utc)


def _retention_days() -> int | None:
    try:
        retention = read_policy().get("human_retention", {})
        if not isinstance(retention, dict):
            return None
        if retention.get("mode") == "forever":
            return _TTL_DAYS
        if retention.get("mode") == "days":
            days = retention.get("days")
            return min(days, _TTL_DAYS) if type(days) is int and days > 0 else None
    except (OSError, TypeError, ValueError):
        pass
    return None


def _check_ref(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CaseDeclarationError(f"invalid opaque {label}")


@contextmanager
def _database(*, create: bool) -> Iterator[sqlite3.Connection | None]:
    path = _path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            yield None
            return
        # auth_dir is private (0700). O_EXCL and O_NOFOLLOW prevent a
        # preexisting link being opened during initial creation on POSIX.
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            pass  # Another process created it; validate below.
        else:
            os.close(fd)
        info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise CaseDeclarationError("unsafe case declaration storage path")
    if os.name == "posix" and (info.st_mode & 0o077):
        raise CaseDeclarationError("case declaration storage permissions are too broad")
    # mode=rw avoids recreating a removed file during a read/revoke race.
    uri = "file:" + quote(str(path), safe="/:") + "?mode=rw"
    db = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and create:
            db.execute("BEGIN IMMEDIATE")
            try:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    db.execute("""CREATE TABLE IF NOT EXISTS declarations (
                        work_item_ref TEXT PRIMARY KEY,
                        scope_ref TEXT NOT NULL,
                        goal_code TEXT NOT NULL,
                        expected_outcome_code TEXT NOT NULL,
                        dependencies_json TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )""")
                    db.execute("PRAGMA user_version=1")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 1:
            raise CaseDeclarationError("unsupported or incomplete declaration store")
        yield db
    finally:
        db.close()


def _reviewed(now: datetime) -> dict[str, datetime]:
    # Preserve each admitting review's exact expiry: a declaration must not
    # outlive the human review that authorized its creation. Merely renewing
    # a lapsed review cannot revive an older declaration.
    result: dict[str, datetime] = {}
    for item in list_review_intentions(now=now)["items"]:
        try:
            expiry = datetime.fromisoformat(item["expires_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if expiry.tzinfo is None or expiry <= now:
            continue
        result[item["work_item_ref"]] = expiry.astimezone(timezone.utc)
    return result


def _prune(db: sqlite3.Connection, now: datetime, days: int, reviewed: dict[str, datetime]) -> None:
    cutoff = (now - timedelta(days=days)).isoformat()
    db.execute("DELETE FROM declarations WHERE expires_at <= ? OR created_at <= ?",
               (now.isoformat(), cutoff))
    # A source disconnect or explicit review deletion removes admission even
    # if a previous cleanup was interrupted. No undeclared orphan can survive
    # the next write transaction.
    if reviewed:
        placeholders = ",".join("?" for _ in reviewed)
        db.execute(f"DELETE FROM declarations WHERE work_item_ref NOT IN ({placeholders})",
                   tuple(sorted(reviewed)))
    else:
        db.execute("DELETE FROM declarations")


def _public(row: tuple[Any, ...]) -> dict[str, Any]:
    ref, scope, goal, outcome, deps, revision, created, updated, expires = row
    return {
        "work_item_ref": ref, "scope_ref": scope,
        "revision": revision,
        "goal_code": goal, "expected_outcome_code": outcome,
        "depends_on_work_item_refs": json.loads(deps),
        "created_at": created, "updated_at": updated, "expires_at": expires,
        "provenance": "explicit_owner_declaration_not_source_verified",
        "source_state": "unknown_until_explicit_recheck",
        "expected_outcome_verified": False,
        "business_obligation_verified": False,
        "customer_outcome_verified": False,
        "agent_execution_authorized": False,
    }


def _get(db: sqlite3.Connection, ref: str) -> tuple[Any, ...] | None:
    return db.execute("SELECT work_item_ref,scope_ref,goal_code,expected_outcome_code,"
                      "dependencies_json,revision,created_at,updated_at,expires_at "
                      "FROM declarations WHERE work_item_ref=?", (ref,)).fetchone()


def save_case_declaration(
    work_item_ref: str, scope_ref: str, *,
    goal_code: str, expected_outcome_code: str,
    depends_on_work_item_refs: list[str] | tuple[str, ...] = (),
    expected_revision: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """CAS-save a declaration; caller must already hold local human review authority.

    The opaque scope must be supplied by a trusted, source-account-bound
    caller. This internal API is deliberately not exposed through REST/MCP.
    """
    _check_ref(work_item_ref, _REF, "work item reference")
    _check_ref(scope_ref, _SCOPE, "source scope")
    if (not isinstance(goal_code, str) or not isinstance(expected_outcome_code, str) or
        goal_code not in _GOALS or expected_outcome_code not in _EXPECTED):
        raise CaseDeclarationError("unsupported declared goal or expected outcome")
    if type(expected_revision) is not int or not 0 <= expected_revision < _MAX_REVISION:
        raise CaseDeclarationError("invalid expected revision")
    if (not isinstance(depends_on_work_item_refs, (list, tuple)) or
        len(depends_on_work_item_refs) > _MAX_DEPENDENCIES):
        raise CaseDeclarationError("too many or invalid dependencies")
    dependencies = list(depends_on_work_item_refs)
    for dep in dependencies:
        _check_ref(dep, _REF, "dependency")
    if len(set(dependencies)) != len(dependencies) or work_item_ref in dependencies:
        raise CaseDeclarationError("duplicate or self-referential dependency")
    current = _current(now)
    days = _retention_days()
    if days is None:
        raise CaseDeclarationError("durable declarations require explicit retained human history")
    reviewed = _reviewed(current)
    if work_item_ref not in reviewed or any(dep not in reviewed for dep in dependencies):
        raise CaseDeclarationError("case or dependency lacks current local review")

    with _database(create=True) as db:
        assert db is not None
        db.execute("BEGIN IMMEDIATE")
        try:
            # Admission is checked twice: the first snapshot avoids creating
            # storage for an unreviewed case, while this check rejects a
            # disconnect/review deletion between that snapshot and BEGIN.
            # This is not a cross-process, multi-store revocation transaction.
            live_reviewed = _reviewed(current)
            if (work_item_ref not in live_reviewed or
                any(dep not in live_reviewed for dep in dependencies)):
                raise CaseDeclarationError("case or dependency review revoked before declaration save")
            live_days = _retention_days()
            if live_days is None:
                raise CaseDeclarationError("durable review retention revoked before declaration save")
            days = min(days, live_days)
            _prune(db, current, days, live_reviewed)
            old = _get(db, work_item_ref)
            if old is None:
                if expected_revision != 0:
                    raise CaseDeclarationError("stale declaration revision")
                count = db.execute("SELECT count(*) FROM declarations").fetchone()[0]
                if count >= _MAX_ITEMS:
                    raise CaseDeclarationError("local declaration limit reached")
                created = current.isoformat()
                expires = min(current + timedelta(days=days),
                              live_reviewed[work_item_ref]).isoformat()
                revision = 1
            else:
                if old[1] != scope_ref:
                    raise CaseDeclarationError("source scope mismatch")
                if old[5] != expected_revision:
                    raise CaseDeclarationError("stale declaration revision")
                created, expires, revision = old[6], old[8], old[5] + 1
            # Dependencies must be declared within the same source scope.
            # Their expiry also caps this declaration's lifetime. Otherwise
            # a later re-review/re-declaration of an expired prerequisite
            # could silently resurrect a dependent owner's stale approval.
            dependency_expiries: list[datetime] = []
            for dep in dependencies:
                other = _get(db, dep)
                if other is None or other[1] != scope_ref:
                    raise CaseDeclarationError("dependency missing in this source scope")
                dependency_expiries.append(datetime.fromisoformat(other[8]))
            # The bounded graph is a DAG, not a time-based inferred join.
            pending, seen = list(dependencies), set()
            while pending:
                dep = pending.pop()
                if dep == work_item_ref:
                    raise CaseDeclarationError("cyclic case dependency")
                if dep in seen:
                    continue
                seen.add(dep)
                linked = _get(db, dep)
                if linked is None or linked[1] != scope_ref:
                    raise CaseDeclarationError("dependency scope changed")
                pending.extend(json.loads(linked[4]))
            # Exact CAS updates may shorten an expiry, never extend it.
            # Retention reductions and the admitting review's deadline
            # remain authoritative even when the owner changes dependencies.
            expires = min(
                datetime.fromisoformat(expires),
                datetime.fromisoformat(created) + timedelta(days=days),
                live_reviewed[work_item_ref],
                *dependency_expiries,
            ).isoformat()
            if old is None:
                db.execute("INSERT INTO declarations VALUES (?,?,?,?,?,?,?,?,?)",
                           (work_item_ref, scope_ref, goal_code, expected_outcome_code,
                            json.dumps(dependencies), revision, created, current.isoformat(), expires))
            else:
                db.execute("UPDATE declarations SET goal_code=?,expected_outcome_code=?,"
                           "dependencies_json=?,revision=?,updated_at=?,expires_at=? "
                           "WHERE work_item_ref=?",
                           (goal_code, expected_outcome_code, json.dumps(dependencies),
                            revision, current.isoformat(), expires, work_item_ref))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        result = _get(db, work_item_ref)
        assert result is not None
        return _public(result)


def list_case_declarations(scope_ref: str, *, now: datetime | None = None) -> dict[str, Any]:
    _check_ref(scope_ref, _SCOPE, "source scope")
    current = _current(now)
    days = _retention_days()
    if days is None:
        # Session-only retention must not keep durable case declarations.
        clear_case_declarations()
        rows: list[tuple[Any, ...]] = []
    else:
        reviewed = _reviewed(current)
        rows = []
        with _database(create=False) as db:
            if db is not None and reviewed:
                cutoff = (current - timedelta(days=days)).isoformat()
                candidates = db.execute(
                    "SELECT work_item_ref,scope_ref,goal_code,expected_outcome_code,"
                    "dependencies_json,revision,created_at,updated_at,expires_at "
                    "FROM declarations WHERE scope_ref=? AND expires_at>? AND created_at>? "
                    "ORDER BY updated_at DESC,work_item_ref LIMIT ?",
                    (scope_ref, current.isoformat(), cutoff, _MAX_ITEMS),
                ).fetchall()
                # A dependency whose own declaration expired or changed scope
                # is not an available owner-declared edge, even if the local
                # review queue still contains that opaque case reference.
                # Close this condition transitively: A expired, B -> A, C -> B
                # must suppress BOTH B and C, never expose C -> hidden B.
                available = {r[0] for r in candidates if r[0] in reviewed}
                while True:
                    supported = {
                        r[0] for r in candidates
                        if r[0] in available and
                        all(dep in available for dep in json.loads(r[4]))
                    }
                    if supported == available:
                        break
                    available = supported
                rows = [r for r in candidates if r[0] in available]
    return {
        "format": "openworkgraph.local-case-declarations.v1",
        "items": [_public(row) for row in rows],
        "source_read_performed": False,
        "source_state": "unknown_until_explicit_recheck",
        "agent_execution_authorized": False,
        "not_synced": True,
    }


def purge_case_declaration(work_item_ref: str) -> None:
    """Called by explicit local review deletion; prune dangling dependency edges."""
    _check_ref(work_item_ref, _REF, "work item reference")
    with _database(create=False) as db:
        if db is None:
            return
        db.execute("BEGIN IMMEDIATE")
        try:
            # A dependency was explicitly owner-declared, not inferred.
            # Removing its reviewed prerequisite must never silently turn a
            # dependent declaration into an independent one. Erase all
            # transitively affected declarations; their owners must review
            # and declare any replacement graph explicitly.
            rows = db.execute(
                "SELECT work_item_ref,dependencies_json FROM declarations"
            ).fetchall()
            revoked = {work_item_ref}
            while True:
                newly_revoked = {
                    ref for ref, raw in rows
                    if ref not in revoked and
                    any(dep in revoked for dep in json.loads(raw))
                }
                if not newly_revoked:
                    break
                revoked.update(newly_revoked)
            for ref in sorted(revoked):
                db.execute("DELETE FROM declarations WHERE work_item_ref=?", (ref,))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise


def clear_case_declarations() -> None:
    """Erase local declarations on source replacement/disconnect or session-only history."""
    path = _path()
    # Do not follow symlinks. SQLite uses DELETE journals, never WAL.
    for candidate in (path, Path(str(path) + "-journal")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


__all__ = ["CaseDeclarationError", "save_case_declaration",
           "list_case_declarations", "purge_case_declaration", "clear_case_declarations"]
