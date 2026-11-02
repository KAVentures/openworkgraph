from __future__ import annotations

"""Private, bounded provider-operation evidence history for reviewed local cases.

The ledger records only independently checked provider state, never a customer
outcome or agent permission. All public operations require a fresh REST-side
owner/DAG/source admission and a transaction-time guard. No provider IDs,
credentials, native records, names, amounts or raw receipts are persisted.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hmac
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any, Callable, Iterator

from .local_auth import auth_dir
from .open_work_case_declarations import CaseDeclarationError, _retention_days
from .open_work_case_status_checkpoint import _bindings, _canonical, _clock, _mac, _parse_utc

_FILENAME = ".open_work_operation_evidence.sqlite3"
_FORMAT = "openworkgraph.local-case-operation-evidence-ledger.v1"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_CASES = 50
_MAX_EVENTS = 32
_REASONS = frozenset({"source_conflict", "record_mismatch", "operator_dispute"})
_FIELDS = ("work_item_ref", "seq", "graph_binding", "source_binding",
           "record_binding", "kind", "provider_state", "provider_verified_at",
           "target_seq", "reason", "checked_at", "expires_at", "prev_mac", "mac")


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
        raise CaseDeclarationError("unsafe private operation ledger")
    for suffix in ("-wal", "-shm"):
        if Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink():
            raise CaseDeclarationError("unsupported operation ledger sidecar")
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
        raise CaseDeclarationError("unsafe operation ledger journal")
    # The file already exists; never let SQLite silently recreate a revoked path.
    from urllib.parse import quote
    db = sqlite3.connect("file:" + quote(str(path), safe="/:") + "?mode=rw",
                         uri=True, isolation_level=None, timeout=5)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise CaseDeclarationError("unsupported operation ledger journal mode")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and create:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 1").fetchone():
                        raise CaseDeclarationError("unversioned operation ledger schema")
                    db.execute("""CREATE TABLE evidence (
                        work_item_ref TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        graph_binding TEXT NOT NULL,
                        source_binding TEXT NOT NULL,
                        record_binding TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        provider_state TEXT,
                        provider_verified_at TEXT,
                        target_seq INTEGER,
                        reason TEXT,
                        checked_at TEXT NOT NULL,
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
            raise CaseDeclarationError("unsupported operation ledger schema")
        columns = [row[1] for row in db.execute("PRAGMA table_xinfo(evidence)").fetchall()]
        objects = db.execute("SELECT name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
        if columns != list(_FIELDS) or objects != [("evidence",)]:
            raise CaseDeclarationError("incompatible operation ledger schema")
        yield db
    finally:
        db.close()


def _context(admitted: dict[str, Any], signature: tuple, scope: str,
             secret: str) -> tuple[str, str, bytes, datetime]:
    graph, source, key = _bindings(signature, scope, secret)
    expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
    if expiry <= _clock(None):
        raise CaseDeclarationError("expired owner review")
    if _retention_days() is None:
        raise CaseDeclarationError("retention does not permit operation evidence")
    return graph, source, key, expiry


def _record(key: bytes, resource_ref: str) -> str:
    if not isinstance(resource_ref, str) or len(resource_ref) != 38 or not (
        resource_ref.startswith("owg:r:") and
        all(c in "0123456789abcdef" for c in resource_ref[6:])
    ):
        raise CaseDeclarationError("invalid exact record reference")
    return _mac(key, b"operation-ledger-record", resource_ref.encode("ascii"))


def _rows(db: sqlite3.Connection, ref: str) -> list[dict[str, Any]]:
    raw = db.execute(
        "SELECT " + ",".join(_FIELDS) +
        " FROM evidence WHERE work_item_ref=? ORDER BY seq LIMIT ?",
        (ref, _MAX_EVENTS + 1),
    ).fetchall()
    if len(raw) > _MAX_EVENTS:
        raise CaseDeclarationError("operation evidence history exceeds capacity")
    return [dict(zip(_FIELDS, row)) for row in raw]


def _validate(rows: list[dict[str, Any]], *, graph: str, source: str,
              key: bytes, now: datetime, days: int) -> None:
    previous = ""
    previous_time = None
    record = None
    disputed: set[int] = set()
    checks: set[int] = set()
    for index, row in enumerate(rows, 1):
        if (row["seq"] != index or row["graph_binding"] != graph or
            row["source_binding"] != source or
            not isinstance(row["record_binding"], str) or
            len(row["record_binding"]) != 64 or
            (record is not None and row["record_binding"] != record) or
            row["prev_mac"] != previous):
            raise CaseDeclarationError("stale or conflicting operation evidence binding")
        record = row["record_binding"]
        stamp = _parse_utc(row["checked_at"])
        expiry = _parse_utc(row["expires_at"])
        if (stamp > now + timedelta(minutes=5) or
            (previous_time is not None and stamp <= previous_time) or
            not stamp < expiry or expiry > stamp + timedelta(days=7) or
            now >= expiry or now - stamp >= timedelta(days=days)):
            raise CaseDeclarationError("stale operation evidence history")
        previous_time = stamp
        if row["kind"] == "check":
            if (row["provider_state"] not in ("paid", "unknown") or
                row["target_seq"] is not None or row["reason"] is not None):
                raise CaseDeclarationError("invalid provider operation check")
            if row["provider_state"] == "paid":
                verified = _parse_utc(row["provider_verified_at"])
                if verified > stamp + timedelta(minutes=5) or stamp - verified > timedelta(minutes=15):
                    raise CaseDeclarationError("stale provider operation timestamp")
            elif row["provider_verified_at"] is not None:
                raise CaseDeclarationError("unknown check cannot carry paid receipt")
            checks.add(index)
        elif row["kind"] == "dispute":
            target = row["target_seq"]
            if (type(target) is not int or target not in checks or target in disputed or
                row["reason"] not in _REASONS or
                row["provider_state"] is not None or row["provider_verified_at"] is not None):
                raise CaseDeclarationError("invalid operation evidence correction")
            disputed.add(target)
        else:
            raise CaseDeclarationError("invalid operation evidence event kind")
        unsigned = {name: row[name] for name in _FIELDS if name != "mac"}
        expected = _mac(key, b"operation-ledger-event", _canonical(unsigned))
        if not isinstance(row["mac"], str) or not hmac.compare_digest(row["mac"], expected):
            raise CaseDeclarationError("operation evidence integrity failure")
        previous = row["mac"]


def _result(ref: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    disputes = {r["target_seq"]: r["reason"] for r in rows if r["kind"] == "dispute"}
    entries = []
    for row in rows:
        entries.append({
            "sequence": row["seq"], "kind": row["kind"],
            "checked_at": row["checked_at"],
            "provider_state": row["provider_state"],
            "provider_verified_at": row["provider_verified_at"],
            "disputes_sequence": row["target_seq"], "dispute_reason": row["reason"],
            "disputed": row["kind"] == "check" and row["seq"] in disputes,
        })
    checks = [r for r in rows if r["kind"] == "check"]
    last = checks[-1] if checks else None
    latest = ("unknown" if last is None or last["seq"] in disputes
              else last["provider_state"])
    return {
        "format": _FORMAT, "work_item_ref": ref, "events": entries,
        "event_count": len(entries), "provider_check_count": len(checks),
        "disputed_check_count": len(disputes),
        "last_observed_provider_state": latest,
        "historical_only_requires_fresh_source_recheck": True,
        "current_provider_operation_verified": False,
        "expected_outcome_verified": False, "customer_outcome_verified": False,
        "business_obligation_verified": False, "agent_caused_operation_verified": False,
        "agent_execution_authorized": False, "source_write_performed": False,
        "server_side_evidence_persisted": bool(rows), "not_synced": True,
    }


def _load(db: sqlite3.Connection, admitted: dict[str, Any], graph: str,
          source: str, key: bytes, expiry: datetime, now: datetime) -> list[dict[str, Any]]:
    ref = admitted["work_item_ref"]
    rows = _rows(db, ref)
    if not rows:
        return []
    days = _retention_days()
    if days is None:
        db.execute("DELETE FROM evidence WHERE work_item_ref=?", (ref,))
        raise CaseDeclarationError("retention revoked operation evidence")
    # Revocation/changed owner DAG cannot resurrect previous evidence on a
    # newly approved case. Remove stale rows rather than remapping their state.
    if (rows[0]["graph_binding"] != graph or rows[0]["source_binding"] != source or
        any(_parse_utc(r["expires_at"]) > expiry or
            now >= _parse_utc(r["expires_at"]) or
            now - _parse_utc(r["checked_at"]) >= timedelta(days=days) for r in rows)):
        db.execute("DELETE FROM evidence WHERE work_item_ref=?", (ref,))
        return []
    _validate(rows, graph=graph, source=source, key=key, now=now, days=days)
    return rows


def _event(ref: str, rows: list[dict[str, Any]], *, graph: str,
           source: str, record: str, key: bytes, expiry: datetime,
           kind: str, state: str | None = None, verified_at: str | None = None,
           target: int | None = None, reason: str | None = None) -> dict[str, Any]:
    now = _clock(None)
    if rows:
        last_time = _parse_utc(rows[-1]["checked_at"])
        if now <= last_time:
            now = last_time + timedelta(microseconds=1)
        if rows[0]["record_binding"] != record:
            raise CaseDeclarationError("conflicting source-joined case record")
    if len(rows) >= _MAX_EVENTS:
        raise CaseDeclarationError("operation evidence history full")
    days = _retention_days()
    if days is None:
        raise CaseDeclarationError("retention revoked operation evidence")
    deadline = min(expiry, now + timedelta(days=days), now + timedelta(days=7))
    if rows:
        deadline = min(deadline, *(_parse_utc(r["expires_at"]) for r in rows))
    if deadline <= now:
        raise CaseDeclarationError("operation evidence expired")
    unsigned = dict(
        work_item_ref=ref, seq=len(rows) + 1, graph_binding=graph,
        source_binding=source, record_binding=record, kind=kind,
        provider_state=state, provider_verified_at=verified_at,
        target_seq=target, reason=reason, checked_at=now.isoformat(),
        expires_at=deadline.isoformat(), prev_mac=rows[-1]["mac"] if rows else "",
    )
    return {**unsigned, "mac": _mac(key, b"operation-ledger-event", _canonical(unsigned))}


def _insert(db: sqlite3.Connection, row: dict[str, Any]) -> None:
    db.execute("INSERT INTO evidence (" + ",".join(_FIELDS) + ") VALUES (" +
               ",".join("?" for _ in _FIELDS) + ")",
               tuple(row[name] for name in _FIELDS))


def _operate(admitted: dict[str, Any], signature: tuple, *, scope: str,
             secret: str, guard: Callable[[], None], mode: str,
             resource_ref: str | None = None, verification: dict | None = None,
             target_seq: int | None = None, reason: str | None = None) -> dict[str, Any]:
    graph, source, key, expiry = _context(admitted, signature, scope, secret)
    ref = admitted["work_item_ref"]
    record = _record(key, resource_ref) if resource_ref is not None else None
    if mode == "check":
        if (not isinstance(verification, dict) or
            verification.get("format") != "openworkgraph.local-case-provider-operation-verification.v1" or
            verification.get("work_item_ref") != ref or
            verification.get("resource_ref") != resource_ref or
            verification.get("expected_operation") != "stripe_invoice_paid" or
            verification.get("provider_operation_state") not in ("paid", "unknown") or
            verification.get("provider_operation_verified") is not
                (verification.get("provider_operation_state") == "paid") or
            any(verification.get(flag) is not False for flag in (
                "customer_outcome_verified", "expected_outcome_verified",
                "agent_caused_operation_verified", "agent_execution_authorized"))):
            raise CaseDeclarationError("invalid independent provider evidence")
        stamp = verification.get("provider_verified_at")
        if verification["provider_operation_state"] == "paid":
            verified = _parse_utc(stamp)
            if verified > _clock(None) + timedelta(minutes=5) or _clock(None) - verified > timedelta(minutes=15):
                raise CaseDeclarationError("stale independent provider receipt")
            stamp = verified.isoformat()
        elif stamp is not None:
            raise CaseDeclarationError("unknown provider operation cannot carry receipt")
    elif mode == "dispute":
        if type(target_seq) is not int or reason not in _REASONS:
            raise CaseDeclarationError("invalid explicit operation dispute")
    elif mode != "history":
        raise CaseDeclarationError("unsupported operation evidence action")
    # Review deletion holds the review lock while purging this SQLite file.
    # Never acquire that review lock while holding our SQLite write lock:
    # the reverse order would deadlock with explicit revocation. Admission
    # before and after the transaction is best-effort local fencing only.
    guard()
    with _database(create=mode == "check") as db:
        if db is None:
            return _result(ref, [])
        db.execute("BEGIN IMMEDIATE")
        try:
            now = _clock(None)
            rows = _load(db, admitted, graph, source, key, expiry, now)
            if mode == "check":
                if not rows and db.execute("SELECT COUNT(DISTINCT work_item_ref) FROM evidence").fetchone()[0] >= _MAX_CASES:
                    raise CaseDeclarationError("operation evidence case capacity reached")
                event = _event(
                    ref, rows, graph=graph, source=source, record=record,
                    key=key, expiry=expiry, kind="check",
                    state=verification["provider_operation_state"],
                    verified_at=stamp,
                )
                _insert(db, event)
                rows.append(event)
            elif mode == "dispute":
                checks = {r["seq"] for r in rows if r["kind"] == "check"}
                disputed = {r["target_seq"] for r in rows if r["kind"] == "dispute"}
                if target_seq not in checks or target_seq in disputed:
                    raise CaseDeclarationError("missing or already disputed provider check")
                if not rows:
                    raise CaseDeclarationError("no source-linked evidence to dispute")
                event = _event(
                    ref, rows, graph=graph, source=source,
                    record=rows[0]["record_binding"], key=key, expiry=expiry,
                    kind="dispute", target=target_seq, reason=reason,
                )
                _insert(db, event)
                rows.append(event)
            result = _result(ref, rows)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    # A revoked review/source is denied even if it changed during the write.
    # Conservatively scrub the entire local evidence store if post-commit
    # admission fails; never leave a just-written stale case on disk.
    try:
        guard()
    except Exception:
        clear_operation_evidence()
        raise
    return result


def append_operation_evidence(admitted: dict[str, Any], signature: tuple, *,
                              scope: str, secret: str, resource_ref: str,
                              verification: dict, guard: Callable[[], None]) -> dict[str, Any]:
    return _operate(admitted, signature, scope=scope, secret=secret,
                    guard=guard, mode="check", resource_ref=resource_ref,
                    verification=verification)


def read_operation_evidence(admitted: dict[str, Any], signature: tuple, *,
                            scope: str, secret: str, guard: Callable[[], None]) -> dict[str, Any]:
    return _operate(admitted, signature, scope=scope, secret=secret,
                    guard=guard, mode="history")


def dispute_operation_evidence(admitted: dict[str, Any], signature: tuple, *,
                               scope: str, secret: str, guard: Callable[[], None],
                               target_seq: int, reason: str) -> dict[str, Any]:
    return _operate(admitted, signature, scope=scope, secret=secret,
                    guard=guard, mode="dispute", target_seq=target_seq, reason=reason)


def clear_operation_evidence(work_item_ref: str | None = None) -> None:
    """Erase all evidence on source disconnect or one revoked review."""
    path = _path()
    if work_item_ref is None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            try:
                if candidate.is_dir() and not candidate.is_symlink():
                    continue  # Never recursively delete an unexpected directory.
                candidate.unlink()
            except (FileNotFoundError, IsADirectoryError):
                pass
        return
    with _database(create=False) as db:
        if db is not None:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM evidence WHERE work_item_ref=?", (work_item_ref,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise


__all__ = ["append_operation_evidence", "read_operation_evidence",
           "dispute_operation_evidence", "clear_operation_evidence"]
