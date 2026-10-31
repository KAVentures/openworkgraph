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
import secrets
import sqlite3
import stat
from typing import Any, Iterator
from urllib.parse import quote

from .local_auth import auth_dir
from .open_work_reviews import list_review_intentions
from shared.history_policy import read_policy

_REF = re.compile(r"^owg:w:[0-9a-f]{32}$")
_SCOPE = re.compile(r"^owg:scope:[0-9a-f]{32}$")
_EDIT_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_GOALS = frozenset({"review_source_state", "assess_declared_policy", "verify_operation"})
_EXPECTED = frozenset({"human_review_recorded", "source_state_rechecked", "operation_receipt_reviewed"})
_FILENAME = ".open_work_case_declarations.sqlite3"
_MAX_ITEMS = 50
_MAX_DEPENDENCIES = 8
_MAX_STORED_DEPENDENCIES_CHARS = 1024  # canonical eight-edge JSON is <=336 chars
_MAX_STORED_TIMESTAMP_CHARS = 40  # canonical UTC writer timestamps are <=32 chars
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
                    # A version-zero file is safe to initialize only when
                    # its user-defined schema is empty. IF NOT EXISTS would
                    # silently bless an unrelated/incomplete declarations
                    # table as v2 and turn a corrupt auxiliary file into
                    # seemingly authoritative owner facts.
                    existing = db.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE name NOT GLOB 'sqlite_*' LIMIT 1"
                    ).fetchone()
                    if existing is not None:
                        raise CaseDeclarationError(
                            "unversioned declaration store has existing schema")
                    db.execute("""CREATE TABLE declarations (
                        work_item_ref TEXT PRIMARY KEY,
                        scope_ref TEXT NOT NULL,
                        goal_code TEXT NOT NULL,
                        expected_outcome_code TEXT NOT NULL,
                        dependencies_json TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        edit_token TEXT NOT NULL
                    )""")
                    db.execute("PRAGMA user_version=2")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 1:
            # The draft v1 ledger had revision-only CAS, which could be
            # replayed after deletion and re-declaration (ABA). Assign each
            # retained row a new opaque incarnation token transactionally.
            # No deleted refs or tombstones are retained by this migration.
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] == 1:
                    # A version marker is not proof of the old declaration
                    # schema. Validate the complete v1 identity contract
                    # *before* ALTER or token generation, including generated/hidden
                    # columns that PRAGMA table_info silently omits; otherwise an
                    # incompatible v1 table can be mutated/stamped as v2
                    # before the later v2 shape check rejects it.
                    legacy_expected_columns = (
                        ("work_item_ref", "TEXT", 0, 1),
                        ("scope_ref", "TEXT", 1, 0),
                        ("goal_code", "TEXT", 1, 0),
                        ("expected_outcome_code", "TEXT", 1, 0),
                        ("dependencies_json", "TEXT", 1, 0),
                        ("revision", "INTEGER", 1, 0),
                        ("created_at", "TEXT", 1, 0),
                        ("updated_at", "TEXT", 1, 0),
                        ("expires_at", "TEXT", 1, 0),
                    )
                    legacy_table = db.execute(
                        "SELECT type FROM sqlite_master WHERE name='declarations'"
                    ).fetchone()
                    legacy_columns = db.execute(
                        "PRAGMA table_xinfo(declarations)"
                    ).fetchall()
                    legacy_actual_columns = tuple(
                        (name, declared_type.upper(), required, primary, hidden)
                        for _index, name, declared_type, required, _default, primary, hidden
                        in legacy_columns
                    )
                    if (legacy_table != ("table",) or
                        legacy_actual_columns != tuple((*column, 0) for column in legacy_expected_columns) or
                        _unexpected_schema_object(db) or _unexpected_schema_constraints(db)):
                        raise CaseDeclarationError("incompatible legacy declaration schema")
                    # Legacy migration must honor the same global 50-row
                    # limit as v2 reads/writes. Check before ALTER/token
                    # generation: damaged v1 databases must not force an
                    # unbounded fetch or silently become accepted v2 stores.
                    # The v1 table uses TEXT PRIMARY KEY (ordinary SQLite
                    # rowid table). Fetch integer rowids, not unbounded native
                    # work_item_ref TEXT cells from a damaged legacy store.
                    # This bounds migration enumeration as well as row count.
                    legacy_rowids = db.execute(
                        "SELECT rowid FROM declarations LIMIT ?",
                        (_MAX_ITEMS + 1,),
                    ).fetchall()
                    if len(legacy_rowids) > _MAX_ITEMS:
                        raise CaseDeclarationError("local declaration ledger exceeds item limit")
                    db.execute("ALTER TABLE declarations ADD COLUMN edit_token TEXT NOT NULL DEFAULT ''")
                    for (rowid,) in legacy_rowids:
                        db.execute(
                            "UPDATE declarations SET edit_token=? WHERE rowid=?",
                            (secrets.token_hex(16), rowid),
                        )
                    db.execute("PRAGMA user_version=2")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 2:
            raise CaseDeclarationError("unsupported or incomplete declaration store")
        # user_version is only a marker, not proof of a compatible ledger.
        # In particular, a missing PRIMARY KEY would permit duplicate
        # work_item_ref rows, making exact CAS and owner approval ambiguous.
        # Check the fixed v2 contract before any read or mutation, including
        # generated/hidden columns omitted by PRAGMA table_info. Both a
        # fresh v2 table and the supported v1 migration have this shape.
        expected_columns = (
            ("work_item_ref", "TEXT", 0, 1),
            ("scope_ref", "TEXT", 1, 0),
            ("goal_code", "TEXT", 1, 0),
            ("expected_outcome_code", "TEXT", 1, 0),
            ("dependencies_json", "TEXT", 1, 0),
            ("revision", "INTEGER", 1, 0),
            ("created_at", "TEXT", 1, 0),
            ("updated_at", "TEXT", 1, 0),
            ("expires_at", "TEXT", 1, 0),
            ("edit_token", "TEXT", 1, 0),
        )
        table = db.execute(
            "SELECT type FROM sqlite_master WHERE name='declarations'"
        ).fetchone()
        columns = db.execute("PRAGMA table_xinfo(declarations)").fetchall()
        actual_columns = tuple(
            (name, declared_type.upper(), required, primary, hidden)
            for _index, name, declared_type, required, _default, primary, hidden in columns
        )
        if (table != ("table",) or actual_columns != tuple((*column, 0) for column in expected_columns) or
            _unexpected_schema_object(db) or _unexpected_schema_constraints(db)):
            raise CaseDeclarationError("incompatible versioned declaration schema")
        yield db
    finally:
        db.close()


def _unexpected_schema_object(db: sqlite3.Connection) -> bool:
    """Reject executable/foreign schema alongside the private declaration table.

    SQLite's implicit PRIMARY KEY autoindex has a reserved sqlite_* name.
    The trusted writer creates no other tables, views, indexes or triggers.
    A trigger is especially dangerous: table_info alone cannot reveal one,
    and it can silently rewrite or erase owner facts during a CAS/migration.
    """
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' "
        "AND NOT (type='table' AND name='declarations') LIMIT 1"
    ).fetchone() is not None


def _unexpected_schema_constraints(db: sqlite3.Connection) -> bool:
    """Reject hidden UNIQUE and FK semantics not present in the private ledger.

    SQLite names implicit UNIQUE indexes sqlite_autoindex_*; checking only
    sqlite_master user objects misses them. Foreign keys add no schema object
    at all and may change deletion semantics or reject otherwise valid saves.
    Both the original v1 table and the v2 writer have exactly one implicit
    work_item_ref PRIMARY KEY index and no foreign keys.
    """
    indexes = db.execute("PRAGMA index_list(declarations)").fetchall()
    foreign_keys = db.execute("PRAGMA foreign_key_list(declarations)").fetchall()
    return (
        indexes != [(0, "sqlite_autoindex_declarations_1", 1, "pk", 0)]
        or bool(foreign_keys)
    )


def _reviewed(now: datetime) -> dict[str, tuple[datetime, datetime]]:
    # Preserve each admitting review's exact expiry: a declaration must not
    # outlive the human review that authorized its creation. Merely renewing
    # a lapsed review cannot revive an older declaration.
    result: dict[str, tuple[datetime, datetime]] = {}
    for item in list_review_intentions(now=now)["items"]:
        try:
            admitted = datetime.fromisoformat(item["created_at"])
            expiry = datetime.fromisoformat(item["expires_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if (admitted.tzinfo is None or expiry.tzinfo is None or
            admitted > now or expiry <= now):
            continue
        # A replacement human review is not a reapproval of an earlier
        # owner-declared goal or dependency graph, even before expiry.
        result[item["work_item_ref"]] = (
            admitted.astimezone(timezone.utc), expiry.astimezone(timezone.utc))
    return result


def _stored_dependencies(raw: str, owner_ref: str) -> list[str] | None:
    """Fail closed on malformed persisted edges; never coerce them to no edges."""
    # SQLite row-count bounds alone do not bound JSON decoding cost. The
    # trusted eight-edge writer emits at most 336 characters; permit bounded
    # legacy whitespace while refusing oversized/corrupt auxiliary payloads
    # before handing them to the decoder. Never reinterpret them as no edges.
    if not isinstance(raw, str) or len(raw) > _MAX_STORED_DEPENDENCIES_CHARS:
        return None
    try:
        dependencies = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None
    if (not isinstance(dependencies, list) or
        len(dependencies) > _MAX_DEPENDENCIES or
        any(not isinstance(dep, str) or not _REF.fullmatch(dep)
            for dep in dependencies) or
        owner_ref in dependencies or
        len(set(dependencies)) != len(dependencies)):
        return None
    return dependencies


def _stored_fields_valid(row: tuple[Any, ...]) -> bool:
    """Validate stored fixed-code declarations before exposing owner approval."""
    ref, scope, goal, outcome, _deps, revision, _created, _updated, _expires, token = row
    return (
        isinstance(ref, str) and _REF.fullmatch(ref) is not None and
        isinstance(scope, str) and _SCOPE.fullmatch(scope) is not None and
        isinstance(goal, str) and goal in _GOALS and
        isinstance(outcome, str) and outcome in _EXPECTED and
        type(revision) is int and 1 <= revision <= _MAX_REVISION and
        isinstance(token, str) and _EDIT_TOKEN.fullmatch(token) is not None
    )


def _stored_temporal_valid(
    created: str, updated: str, expires: str, *,
    now: datetime, days: int, review: tuple[datetime, datetime],
) -> bool:
    """Reject corrupt/future approval clocks rather than trusting text ordering.

    The internal writer persists UTC ISO-8601 timestamps. A damaged auxiliary
    row must never extend an owner approval, silently outlive its admitting
    review, or crash a bounded list/save while hiding dependent invalidation.
    """
    stamps = (created, updated, expires)
    # Reject damaged auxiliary TEXT before invoking the ISO parser. The
    # trusted writer emits at most 32 characters for a UTC timestamp; this
    # small margin accommodates bounded legacy formatting, not arbitrary
    # attacker-sized SQLite cells. SQLite has already materialized the row.
    if any(not isinstance(value, str) or len(value) > _MAX_STORED_TIMESTAMP_CHARS
           for value in stamps):
        return False
    try:
        parsed = tuple(datetime.fromisoformat(value) for value in stamps)
    except (TypeError, ValueError):
        return False
    if any(not isinstance(raw, str) or stamp.tzinfo is None or
           stamp.utcoffset() != timedelta(0) or stamp.isoformat() != raw
           for raw, stamp in zip(stamps, parsed)):
        return False
    start, revision, deadline = parsed
    return (review[0] <= start <= revision <= now < deadline <= review[1] and
            start > now - timedelta(days=days) and
            deadline <= start + timedelta(days=days))


def _dependency_approval_window_valid(
    dependent: tuple[Any, ...], prerequisite: tuple[Any, ...],
) -> bool:
    """A declared edge cannot outlive or predate its prerequisite approval.

    Call only after both stored rows passed canonical UTC temporal validation.
    Comparing parsed instants avoids ISO text-order errors at fractional seconds.
    This validates an existing explicit edge; it never infers a time-based join.
    """
    return (
        datetime.fromisoformat(prerequisite[7]) <= datetime.fromisoformat(dependent[7])
        and datetime.fromisoformat(dependent[8]) <= datetime.fromisoformat(prerequisite[8])
    )


def _prune(db: sqlite3.Connection, now: datetime, days: int,
           reviewed: dict[str, tuple[datetime, datetime]]) -> None:
    # Admit only a DAG rooted in current, same-scope, explicitly reviewed
    # declarations. A malformed row, orphan, or corrupt cycle cannot become
    # an independent approved case or revive its dependent closure on writes.
    rows = db.execute(
        "SELECT work_item_ref,scope_ref,goal_code,expected_outcome_code,"
        "dependencies_json,revision,created_at,updated_at,expires_at,edit_token "
        "FROM declarations LIMIT ?", (_MAX_ITEMS + 1,)
    ).fetchall()
    if len(rows) > _MAX_ITEMS:
        # Reject damaged over-capacity ledgers before materializing a graph.
        raise CaseDeclarationError("local declaration ledger exceeds item limit")
    scopes = {row[0]: row[1] for row in rows}
    row_by_ref = {row[0]: row for row in rows}
    dependencies = {
        row[0]: _stored_dependencies(row[4], row[0]) for row in rows
    }
    eligible = {
        row[0] for row in rows
        if (_stored_fields_valid(row) and row[0] in reviewed and
            dependencies[row[0]] is not None and
            _stored_temporal_valid(row[6], row[7], row[8],
                                   now=now, days=days, review=reviewed[row[0]]))
    }
    available: set[str] = set()
    while True:
        newly_available = {
            row[0] for row in rows
            if row[0] in eligible and row[0] not in available and
            all(dep in available and scopes.get(dep) == row[1] and
                _dependency_approval_window_valid(row, row_by_ref[dep])
                for dep in dependencies[row[0]])
        }
        if not newly_available:
            break
        available.update(newly_available)
    # SQLite TEXT-affinity columns can still contain BLOB keys in a damaged
    # auxiliary ledger. Such keys are ineligible, but sorting them beside
    # valid string refs raises TypeError and prevents authorized pruning.
    # Deletion order has no semantic significance inside this transaction.
    for ref in scopes.keys() - available:
        db.execute("DELETE FROM declarations WHERE work_item_ref=?", (ref,))

def _public(row: tuple[Any, ...]) -> dict[str, Any]:
    ref, scope, goal, outcome, deps, revision, created, updated, expires, edit_token = row
    return {
        "work_item_ref": ref, "scope_ref": scope,
        "revision": revision,
        "edit_token": edit_token,
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
                      "dependencies_json,revision,created_at,updated_at,expires_at,edit_token "
                      "FROM declarations WHERE work_item_ref=?", (ref,)).fetchone()


def _dependent_closure(db: sqlite3.Connection, work_item_ref: str) -> set[str]:
    """All transitively dependent declarations, including the root.

    Owner approval of B -> A is tied to the current revision of A. If A
    changes, retaining B would silently carry approval to a different
    declared goal/outcome/dependency graph. The store has at most 50 rows.
    """
    rows = db.execute(
        "SELECT work_item_ref,dependencies_json FROM declarations LIMIT ?",
        (_MAX_ITEMS + 1,),
    ).fetchall()
    if len(rows) > _MAX_ITEMS:
        # Review deletion falls back to scrubbing the corrupt auxiliary DB.
        raise CaseDeclarationError("local declaration ledger exceeds item limit")
    edges = []
    for ref, raw in rows:
        dependencies = _stored_dependencies(raw, ref)
        if dependencies is None:
            raise CaseDeclarationError("invalid stored dependency graph")
        edges.append((ref, dependencies))
    affected = {work_item_ref}
    while True:
        newly_affected = {
            ref for ref, dependencies in edges
            if ref not in affected and
            any(dep in affected for dep in dependencies)
        }
        if not newly_affected:
            return affected
        affected.update(newly_affected)


def save_case_declaration(
    work_item_ref: str, scope_ref: str, *,
    goal_code: str, expected_outcome_code: str,
    depends_on_work_item_refs: list[str] | tuple[str, ...] = (),
    expected_revision: int = 0,
    expected_edit_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """CAS-save a declaration; caller must already hold local human review authority.

    The opaque scope must be supplied by a trusted, source-account-bound
    caller. This internal API is deliberately not exposed through REST/MCP.
    Updates require both the exact revision and the edit_token from the same
    returned incarnation. Tokens are concurrency guards, not permissions.
    """
    _check_ref(work_item_ref, _REF, "work item reference")
    _check_ref(scope_ref, _SCOPE, "source scope")
    if (not isinstance(goal_code, str) or not isinstance(expected_outcome_code, str) or
        goal_code not in _GOALS or expected_outcome_code not in _EXPECTED):
        raise CaseDeclarationError("unsupported declared goal or expected outcome")
    if type(expected_revision) is not int or not 0 <= expected_revision < _MAX_REVISION:
        raise CaseDeclarationError("invalid expected revision")
    if expected_revision == 0:
        if expected_edit_token is not None:
            raise CaseDeclarationError("unexpected edit token on new declaration")
    elif (not isinstance(expected_edit_token, str) or
          not _EDIT_TOKEN.fullmatch(expected_edit_token)):
        # A revision alone cannot distinguish a deleted/redeclared row from
        # its predecessor. Missing tokens fail closed even for legacy callers.
        raise CaseDeclarationError("stale declaration edit token")
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
            # A review can be replaced after the first admission snapshot,
            # without ever disappearing from the queue. Refuse that change
            # rather than attaching a stale declaration to a new review.
            if any(reviewed[ref] != live_reviewed[ref]
                   for ref in (work_item_ref, *dependencies)):
                raise CaseDeclarationError("case or dependency review changed before declaration save")
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
                              live_reviewed[work_item_ref][1]).isoformat()
                revision = 1
            else:
                if old[1] != scope_ref:
                    raise CaseDeclarationError("source scope mismatch")
                if old[5] != expected_revision or old[9] != expected_edit_token:
                    raise CaseDeclarationError("stale declaration revision or edit token")
                # An edit cannot retroactively claim owner approval before the
                # original declaration or its latest approved revision.
                if (datetime.fromisoformat(old[6]) > current or
                    datetime.fromisoformat(old[7]) > current):
                    raise CaseDeclarationError("backdated declaration edit")
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
                # A dependent's approval must not precede the prerequisite's
                # creation or most recent owner-approved revision.
                if (datetime.fromisoformat(other[6]) > current or
                    datetime.fromisoformat(other[7]) > current):
                    raise CaseDeclarationError("dependency declaration newer than approval time")
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
                linked_dependencies = _stored_dependencies(linked[4], dep)
                if linked_dependencies is None:
                    raise CaseDeclarationError("invalid stored dependency graph")
                pending.extend(linked_dependencies)
            # Exact CAS updates may shorten an expiry, never extend it.
            # Retention reductions and the admitting review's deadline
            # remain authoritative even when the owner changes dependencies.
            expires = min(
                datetime.fromisoformat(expires),
                datetime.fromisoformat(created) + timedelta(days=days),
                live_reviewed[work_item_ref][1],
                *dependency_expiries,
            ).isoformat()
            if old is None:
                db.execute("INSERT INTO declarations VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (work_item_ref, scope_ref, goal_code, expected_outcome_code,
                            json.dumps(dependencies), revision, created, current.isoformat(),
                            expires, secrets.token_hex(16)))
            else:
                db.execute("UPDATE declarations SET goal_code=?,expected_outcome_code=?,"
                           "dependencies_json=?,revision=?,updated_at=?,expires_at=? "
                           "WHERE work_item_ref=?",
                           (goal_code, expected_outcome_code, json.dumps(dependencies),
                            revision, current.isoformat(), expires, work_item_ref))
                # A dependent owner's approval was for the prerequisite's
                # previous exact revision. Invalidate the dependent closure
                # atomically with this successful CAS update; never silently
                # rebind owner-declared edges to a revised prerequisite.
                for dependent in sorted(_dependent_closure(db, work_item_ref) - {work_item_ref}):
                    db.execute("DELETE FROM declarations WHERE work_item_ref=?", (dependent,))
            # Read the exact row written by this transaction *before*
            # releasing SQLite's write lock. A concurrent writer may revise
            # or delete it immediately after COMMIT; the response must not
            # claim that other writer's revision/token as our own, or crash.
            saved = _get(db, work_item_ref)
            if saved is None:
                raise CaseDeclarationError("saved declaration unavailable")
            # The review queue and history policy live outside this SQLite
            # transaction. Revalidate immediately before COMMIT: a separate
            # revocation/replacement after the BEGIN check must not cause us
            # to commit a newly approved owner declaration knowingly stale.
            # This narrows a race; it is NOT an atomic cross-store grant fence.
            final_reviewed = _reviewed(current)
            if any(final_reviewed.get(ref) != live_reviewed[ref]
                   for ref in (work_item_ref, *dependencies)):
                raise CaseDeclarationError("case or dependency review changed before declaration commit")
            if _retention_days() != live_days:
                raise CaseDeclarationError("human history retention changed before declaration commit")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        return _public(saved)


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
        candidates: list[tuple[Any, ...]] = []
        with _database(create=False) as db:
            if (db is not None and reviewed and
                db.execute("SELECT COUNT(*) FROM declarations").fetchone()[0] <= _MAX_ITEMS):
                # A scoped subset cannot certify a damaged global ledger.
                cutoff = (current - timedelta(days=days)).isoformat()
                candidates = db.execute(
                    "SELECT work_item_ref,scope_ref,goal_code,expected_outcome_code,"
                    "dependencies_json,revision,created_at,updated_at,expires_at,edit_token "
                    "FROM declarations WHERE scope_ref=? AND expires_at>? AND created_at>? "
                    "AND created_at<=? AND updated_at<=? "
                    "ORDER BY updated_at DESC,work_item_ref LIMIT ?",
                    (scope_ref, current.isoformat(), cutoff,
                     current.isoformat(), current.isoformat(), _MAX_ITEMS),
                ).fetchall()
        # A review can be removed/replaced or retention reduced while the
        # bounded SQLite read is in progress. Recheck after closing the DB,
        # require the same admitting review window, and apply the stricter
        # retention before returning any owner-approved dependency closure.
        # This narrows TOCTOU, not a cross-process source-grant fence.
        live_days = _retention_days()
        if live_days is None:
            clear_case_declarations()
        elif candidates:
            days = min(days, live_days)
            live_reviewed = _reviewed(current)
            reviewed = {
                ref: window for ref, window in reviewed.items()
                if live_reviewed.get(ref) == window
            }
            # A dependency whose own declaration expired or changed scope
            # is not an available owner-declared edge, even if the local
            # review queue still contains that opaque case reference.
            # Close this condition transitively: A expired, B -> A, C -> B
            # must suppress BOTH B and C, never expose C -> hidden B.
            dependencies = {
                r[0]: _stored_dependencies(r[4], r[0]) for r in candidates
            }
            row_by_ref = {r[0]: r for r in candidates}
            eligible = {
                r[0] for r in candidates
                if _stored_fields_valid(r) and r[0] in reviewed and
                dependencies[r[0]] is not None and
                _stored_temporal_valid(r[6], r[7], r[8], now=current,
                                       days=days, review=reviewed[r[0]])
            }
            # Grow from independent reviewed roots. Starting with every
            # row would incorrectly retain a corrupt A <-> B cycle.
            available: set[str] = set()
            while True:
                newly_available = {
                    r[0] for r in candidates
                    if r[0] in eligible and r[0] not in available and
                    all(dep in available and
                        _dependency_approval_window_valid(r, row_by_ref[dep])
                        for dep in dependencies[r[0]])
                }
                if not newly_available:
                    break
                available.update(newly_available)
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
            # A damaged auxiliary SQLite row can contain a BLOB ref even
            # in a TEXT-affinity column. Explicit review deletion must not
            # fail on sorted(str, bytes), which would force the review
            # subsystem to scrub otherwise independent declarations.
            # Dependency closure is a set; deletion order is irrelevant.
            for ref in _dependent_closure(db, work_item_ref):
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
