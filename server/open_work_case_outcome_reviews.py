from __future__ import annotations

"""Private, append-only HUMAN outcome assessments for reviewed local Open Work.

These are owner assertions, never independent provider/customer verification,
business obligations, or agent permissions. No customer/native identifiers,
free text, credentials or source receipts are stored.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hmac
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any, Callable, Iterator

from .local_auth import auth_dir
from .open_work_case_declarations import CaseDeclarationError, _retention_days
from .open_work_case_status_checkpoint import _bindings, _canonical, _clock, _mac, _parse_utc

_FORMAT = "openworkgraph.local-case-human-outcome-assessments.v1"
_FILENAME = ".open_work_human_outcomes.sqlite3"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_CASES = 50
_MAX_EVENTS = 32
_STATUSES = frozenset({"met", "not_met", "unknown"})
_REASONS = frozenset({"owner_review", "customer_feedback_reviewed", "external_evidence_reviewed"})
_FIELDS = ("work_item_ref", "seq", "graph_binding", "source_binding",
           "kind", "human_status", "reason_code", "target_seq",
           "assessed_at", "expires_at", "prev_mac", "mac")


def _path() -> Path:
    return auth_dir() / _FILENAME


@contextmanager
def _database(*, create: bool) -> Iterator[sqlite3.Connection | None]:
    path = _path()
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
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or
        info.st_nlink != 1 or info.st_size > _MAX_BYTES or
        (os.name == "posix" and info.st_mode & 0o077)):
        raise CaseDeclarationError("unsafe human outcome ledger")
    for suffix in ("-wal", "-shm"):
        if Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink():
            raise CaseDeclarationError("unsupported human outcome ledger sidecar")
    journal = Path(str(path) + "-journal")
    try:
        ji = journal.lstat()
    except FileNotFoundError:
        ji = None
    if ji is not None and (
        not stat.S_ISREG(ji.st_mode) or journal.is_symlink() or
        ji.st_nlink != 1 or ji.st_size > 2 * _MAX_BYTES or
        (os.name == "posix" and ji.st_mode & 0o077)
    ):
        raise CaseDeclarationError("unsafe human outcome ledger journal")
    from urllib.parse import quote
    db = sqlite3.connect("file:" + quote(str(path), safe="/:") + "?mode=rw",
                         uri=True, isolation_level=None, timeout=5)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise CaseDeclarationError("unsupported human outcome journal mode")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and create:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 1").fetchone():
                        raise CaseDeclarationError("unversioned human outcome ledger")
                    db.execute("""CREATE TABLE assessments (
                        work_item_ref TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        graph_binding TEXT NOT NULL,
                        source_binding TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        human_status TEXT NOT NULL,
                        reason_code TEXT NOT NULL,
                        target_seq INTEGER,
                        assessed_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        prev_mac TEXT NOT NULL,
                        mac TEXT NOT NULL,
                        PRIMARY KEY(work_item_ref, seq)
                    )""")
                    db.execute("PRAGMA user_version=1")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 1:
            raise CaseDeclarationError("unsupported human outcome schema")
        columns = [row[1] for row in db.execute("PRAGMA table_xinfo(assessments)")]
        objects = db.execute("SELECT name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
        if columns != list(_FIELDS) or objects != [("assessments",)]:
            raise CaseDeclarationError("incompatible human outcome schema")
        yield db
    finally:
        db.close()


def _rows(db: sqlite3.Connection, ref: str) -> list[dict[str, Any]]:
    raw = db.execute(
        "SELECT " + ",".join(_FIELDS) + " FROM assessments "
        "WHERE work_item_ref=? ORDER BY seq LIMIT ?", (ref, _MAX_EVENTS + 1),
    ).fetchall()
    if len(raw) > _MAX_EVENTS:
        raise CaseDeclarationError("human outcome history exceeds capacity")
    return [dict(zip(_FIELDS, row)) for row in raw]


def _validate(rows: list[dict[str, Any]], *, graph: str, source: str,
              key: bytes, now: datetime, days: int) -> None:
    previous = ""
    last_time = None
    for index, row in enumerate(rows, 1):
        if (row["seq"] != index or row["graph_binding"] != graph or
            row["source_binding"] != source or row["prev_mac"] != previous or
            row["human_status"] not in _STATUSES):
            raise CaseDeclarationError("conflicting human outcome evidence binding")
        stamp = _parse_utc(row["assessed_at"])
        expiry = _parse_utc(row["expires_at"])
        if (stamp > now + timedelta(minutes=5) or
            (last_time is not None and stamp <= last_time) or
            stamp >= expiry or expiry > stamp + timedelta(days=7) or
            now >= expiry or now - stamp >= timedelta(days=days)):
            raise CaseDeclarationError("stale human outcome assessment")
        last_time = stamp
        if row["kind"] == "assessment":
            if row["target_seq"] is not None or row["reason_code"] not in _REASONS:
                raise CaseDeclarationError("invalid human outcome assessment")
        elif row["kind"] == "correction":
            if (index < 2 or row["target_seq"] != index - 1 or
                row["reason_code"] != "explicit_correction"):
                raise CaseDeclarationError("invalid human outcome correction")
        else:
            raise CaseDeclarationError("invalid human outcome event kind")
        unsigned = {field: row[field] for field in _FIELDS if field != "mac"}
        expected = _mac(key, b"human-outcome-event", _canonical(unsigned))
        if not isinstance(row["mac"], str) or not hmac.compare_digest(row["mac"], expected):
            raise CaseDeclarationError("human outcome evidence integrity failure")
        previous = row["mac"]


def _result(ref: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": _FORMAT, "work_item_ref": ref,
        "events": [{
            "sequence": row["seq"], "kind": row["kind"],
            "human_assessed_status": row["human_status"],
            "reason_code": row["reason_code"], "corrects_sequence": row["target_seq"],
            "assessed_at": row["assessed_at"],
        } for row in rows],
        "event_count": len(rows),
        "latest_human_assessed_status": rows[-1]["human_status"] if rows else "unknown",
        "provenance": "explicit_local_human_assessment_not_independently_verified",
        "expected_outcome_verified": False, "customer_outcome_verified": False,
        "provider_operation_verified": False, "business_obligation_verified": False,
        "agent_caused_outcome_verified": False, "agent_execution_authorized": False,
        "source_read_performed": False, "source_write_performed": False,
        "server_side_assessment_persisted": bool(rows), "not_synced": True,
    }


def _operate(admitted: dict[str, Any], signature: tuple, *, scope: str,
             secret: str, guard: Callable[[], None], mode: str,
             human_status: str | None = None, reason_code: str | None = None,
             target_seq: int | None = None) -> dict[str, Any]:
    graph, source, key = _bindings(signature, scope, secret)
    ref = admitted["work_item_ref"]
    expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
    days = _retention_days()
    if days is None or expiry <= _clock(None):
        raise CaseDeclarationError("review retention does not permit human outcome history")
    if mode == "assessment":
        if human_status not in _STATUSES or reason_code not in _REASONS:
            raise CaseDeclarationError("invalid human outcome assessment")
    elif mode == "correction":
        if human_status not in _STATUSES or reason_code != "explicit_correction" or type(target_seq) is not int:
            raise CaseDeclarationError("invalid explicit human outcome correction")
    elif mode != "history":
        raise CaseDeclarationError("unsupported human outcome operation")
    # Never acquire the review lock while holding the SQLite write lock.
    guard()
    with _database(create=mode == "assessment") as db:
        if db is None:
            if mode == "correction":
                raise CaseDeclarationError("no human outcome assessment to correct")
            return _result(ref, [])
        db.execute("BEGIN IMMEDIATE")
        try:
            now = _clock(None)
            rows = _rows(db, ref)
            if rows and (rows[0]["graph_binding"] != graph or
                         rows[0]["source_binding"] != source or
                         any(_parse_utc(r["expires_at"]) > expiry or
                             now >= _parse_utc(r["expires_at"]) or
                             now - _parse_utc(r["assessed_at"]) >= timedelta(days=days)
                             for r in rows)):
                db.execute("DELETE FROM assessments WHERE work_item_ref=?", (ref,))
                rows = []
            _validate(rows, graph=graph, source=source, key=key, now=now, days=days)
            if mode != "history":
                if mode == "correction" and (not rows or target_seq != rows[-1]["seq"]):
                    raise CaseDeclarationError("stale human outcome correction target")
                if len(rows) >= _MAX_EVENTS:
                    raise CaseDeclarationError("human outcome history full")
                if not rows and db.execute("SELECT COUNT(DISTINCT work_item_ref) FROM assessments").fetchone()[0] >= _MAX_CASES:
                    raise CaseDeclarationError("human outcome case capacity reached")
                stamp = now
                if rows and stamp <= _parse_utc(rows[-1]["assessed_at"]):
                    stamp = _parse_utc(rows[-1]["assessed_at"]) + timedelta(microseconds=1)
                deadline = min(expiry, stamp + timedelta(days=days), stamp + timedelta(days=7))
                if rows:
                    deadline = min(deadline, *(_parse_utc(r["expires_at"]) for r in rows))
                if deadline <= stamp:
                    raise CaseDeclarationError("human outcome retention expired")
                unsigned = dict(
                    work_item_ref=ref, seq=len(rows) + 1, graph_binding=graph,
                    source_binding=source, kind=mode, human_status=human_status,
                    reason_code=reason_code, target_seq=target_seq if mode == "correction" else None,
                    assessed_at=stamp.isoformat(), expires_at=deadline.isoformat(),
                    prev_mac=rows[-1]["mac"] if rows else "",
                )
                row = {**unsigned, "mac": _mac(key, b"human-outcome-event", _canonical(unsigned))}
                db.execute(
                    "INSERT INTO assessments (" + ",".join(_FIELDS) + ") VALUES (" +
                    ",".join("?" for _ in _FIELDS) + ")",
                    tuple(row[name] for name in _FIELDS),
                )
                rows.append(row)
            result = _result(ref, rows)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    try:
        guard()
    except Exception:
        clear_human_outcome_reviews()
        raise
    return result


def record_human_outcome(admitted: dict[str, Any], signature: tuple, *,
                         scope: str, secret: str, guard: Callable[[], None],
                         human_status: str, reason_code: str) -> dict[str, Any]:
    return _operate(admitted, signature, scope=scope, secret=secret, guard=guard,
                    mode="assessment", human_status=human_status, reason_code=reason_code)


def correct_human_outcome(admitted: dict[str, Any], signature: tuple, *,
                          scope: str, secret: str, guard: Callable[[], None],
                          target_seq: int, human_status: str) -> dict[str, Any]:
    return _operate(admitted, signature, scope=scope, secret=secret, guard=guard,
                    mode="correction", human_status=human_status,
                    reason_code="explicit_correction", target_seq=target_seq)


def read_human_outcomes(admitted: dict[str, Any], signature: tuple, *,
                        scope: str, secret: str, guard: Callable[[], None]) -> dict[str, Any]:
    return _operate(admitted, signature, scope=scope, secret=secret,
                    guard=guard, mode="history")



def record_human_outcomes_batch(
    targets: list[tuple[dict[str, Any], tuple, Callable[[], None],
                        str, str, int]], *, scope: str, secret: str,
) -> list[dict[str, Any]]:
    """Append 1–4 explicit, independent human assessments atomically.

    Every target is already owner/DAG-admitted by the REST caller. The expected
    retained event count is a per-case optimistic concurrency precondition.
    This is a local SQLite atomic write, NOT a cross-process revocation fence,
    provider verification, or a customer/business outcome certificate.
    """
    if not isinstance(targets, list) or not 1 <= len(targets) <= 4:
        raise CaseDeclarationError("invalid bounded human assessment batch")
    days = _retention_days()
    if days is None:
        raise CaseDeclarationError("review retention does not permit human outcome history")
    prepared = []
    seen: set[str] = set()
    for admitted, signature, guard, status, reason, expected_count in targets:
        ref = admitted["work_item_ref"]
        if (ref in seen or status not in _STATUSES or reason not in _REASONS or
                type(expected_count) is not int or not 0 <= expected_count < _MAX_EVENTS):
            raise CaseDeclarationError("invalid or duplicate human assessment selection")
        seen.add(ref)
        graph, source, key = _bindings(signature, scope, secret)
        expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
        if expiry <= _clock(None):
            raise CaseDeclarationError("stale reviewed declaration expiry")
        prepared.append((ref, graph, source, key, expiry, guard,
                         status, reason, expected_count))
    # Do not acquire the review lock while holding the SQLite write lock.
    for item in prepared:
        item[5]()
    with _database(create=True) as db:
        assert db is not None
        db.execute("BEGIN IMMEDIATE")
        try:
            now = _clock(None)
            total_cases = db.execute(
                "SELECT COUNT(DISTINCT work_item_ref) FROM assessments"
            ).fetchone()[0]
            rows_by_ref = []
            new_cases = 0
            # Validate *all* histories and capacity before the first insert.
            for ref, graph, source, key, expiry, _guard, status, reason, expected in prepared:
                rows = _rows(db, ref)
                if rows and (rows[0]["graph_binding"] != graph or
                             rows[0]["source_binding"] != source or
                             any(_parse_utc(r["expires_at"]) > expiry or
                                 now >= _parse_utc(r["expires_at"]) or
                                 now - _parse_utc(r["assessed_at"]) >= timedelta(days=days)
                                 for r in rows)):
                    raise CaseDeclarationError("stale human outcome history")
                _validate(rows, graph=graph, source=source, key=key,
                          now=now, days=days)
                if len(rows) != expected:
                    raise CaseDeclarationError("changed human outcome event count")
                if not rows:
                    new_cases += 1
                rows_by_ref.append(rows)
            if total_cases + new_cases > _MAX_CASES:
                raise CaseDeclarationError("human outcome case capacity reached")
            results = []
            for (ref, graph, source, key, expiry, _guard, status, reason,
                 _expected), rows in zip(prepared, rows_by_ref):
                stamp = now
                if rows and stamp <= _parse_utc(rows[-1]["assessed_at"]):
                    stamp = _parse_utc(rows[-1]["assessed_at"]) + timedelta(microseconds=1)
                deadline = min(expiry, stamp + timedelta(days=days),
                               stamp + timedelta(days=7))
                if rows:
                    deadline = min(deadline, *(_parse_utc(r["expires_at"]) for r in rows))
                if deadline <= stamp:
                    raise CaseDeclarationError("human outcome retention expired")
                unsigned = dict(
                    work_item_ref=ref, seq=len(rows) + 1,
                    graph_binding=graph, source_binding=source,
                    kind="assessment", human_status=status,
                    reason_code=reason, target_seq=None,
                    assessed_at=stamp.isoformat(), expires_at=deadline.isoformat(),
                    prev_mac=rows[-1]["mac"] if rows else "",
                )
                row = {**unsigned, "mac": _mac(key, b"human-outcome-event", _canonical(unsigned))}
                db.execute(
                    "INSERT INTO assessments (" + ",".join(_FIELDS) + ") VALUES (" +
                    ",".join("?" for _ in _FIELDS) + ")",
                    tuple(row[name] for name in _FIELDS),
                )
                results.append(_result(ref, [*rows, row]))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    try:
        for item in prepared:
            item[5]()
    except Exception:
        clear_human_outcome_reviews()
        raise
    return results


def clear_human_outcome_reviews(work_item_ref: str | None = None) -> None:
    path = _path()
    if work_item_ref is None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            try:
                if candidate.is_dir() and not candidate.is_symlink():
                    continue
                candidate.unlink()
            except (FileNotFoundError, IsADirectoryError):
                pass
        return
    with _database(create=False) as db:
        if db is not None:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM assessments WHERE work_item_ref=?", (work_item_ref,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise


__all__ = ["record_human_outcome", "correct_human_outcome",
           "read_human_outcomes", "record_human_outcomes_batch", "clear_human_outcome_reviews"]
