"""Operator-invoked ServiceNow snapshot journal with durable replay dedupe.

This is not a history feed, automatic watermark, scheduled sync, or provider
authorization service. Uses the existing bounded selected-record HTTPS reader.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from urllib.parse import quote

from .servicenow_incident_staging import SelectedIncidents, StagingError
from .servicenow_incident_sync import (
    SyncError, _check_grant, _publish_new, project_csv, read_selected_once,
)

MAX_EVENTS = 4096
MAX_DB_BYTES = 8 * 1024 * 1024
_FIELDS = ("case_id", "activity", "timestamp", "source_event_id")
_HASH = re.compile(r"(?:snowcase|snowevent):[0-9a-f]{64}\Z")


class JournalError(ValueError):
    """Private local journal refusal; no source identifiers in messages."""


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise JournalError("Journal and output directories must not be symlinks")
    try:
        info = path.stat()
    except OSError:
        raise JournalError("Private journal/output directory unavailable") from None
    if not stat.S_ISDIR(info.st_mode):
        raise JournalError("Private journal/output directory required")
    if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
        raise JournalError("Journal/output directory must be owner-only")


def _checked_journal_file(path: Path) -> bool:
    """Create only a new 0600 journal, or validate an existing regular file.

    The private owner-only parent prevents another local user from swapping the
    path between the fd check and SQLite's path-based connection. This is not
    protection against a malicious process running as the same OS user.
    """
    _private_directory(path.parent)
    if path.is_symlink():
        raise JournalError("Journal symlink refused")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        with os.fdopen(fd, "rb", closefd=True) as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size > MAX_DB_BYTES):
                raise JournalError("Invalid or oversized journal file")
            if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
                raise JournalError("Journal file must be owner-only")
    except (OSError, ValueError):
        raise JournalError("Could not safely open private journal") from None
    return created


def _scope_digest(scope: SelectedIncidents, timezone_name: str) -> str:
    # The digest binds the exact selected IDs, not just the tenant namespace.
    # Neither provider IDs nor the tenant label are stored in the journal.
    encoded = json.dumps(
        [scope.host, scope.tenant_scope, sorted(scope.sys_ids), timezone_name],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _csv_rows(text: str) -> list[tuple[str, str, str, str]]:
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = list(reader)
    if not rows or tuple(rows[0]) != _FIELDS or len(rows) > 9:
        raise JournalError("Invalid bounded selected-source CSV")
    result = []
    for row in rows[1:]:
        if (len(row) != 4 or not _HASH.fullmatch(row[0])
                or not _HASH.fullmatch(row[3])
                or not row[0].startswith("snowcase:")
                or not row[3].startswith("snowevent:")
                or not row[1].startswith("Incident state: ")
                or len(row[1]) > 80 or "\n" in row[1] or "\r" in row[1]
                or len(row[2]) > 40):
            raise JournalError("Invalid bounded selected-source event")
        result.append(tuple(row))
    return result


def _export_csv(rows: list[tuple[str, str, str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(_FIELDS)
    writer.writerows(sorted(rows, key=lambda r: (r[0], r[2], r[3])))
    return output.getvalue()


def _connect(path: Path) -> sqlite3.Connection:
    # mode=rw forbids SQLite silently creating a new file if a path disappears.
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=rw"
    db = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA secure_delete=ON")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA journal_mode=DELETE")
    return db


def update_journal(
    scope: SelectedIncidents, *, timezone_name: str, journal_file: Path,
    grant_file: Path, incoming: list[tuple[str, str, str, str]] | None,
) -> tuple[list[tuple[str, str, str, str]], int, int]:
    """Transactional, bounded idempotent append; None means export-only.

    Each revision key must map to identical event contents across all retries.
    Journal commit precedes independent CSV publication. If publication fails,
    --export-only recovers the committed data without a provider request.
    """
    scope.require_authority()
    _check_grant(grant_file, scope)
    if type(timezone_name) is not str or not timezone_name:
        raise JournalError("Explicit source timezone required")
    if incoming is not None and (type(incoming) is not list or len(incoming) > 8):
        raise JournalError("Selected-source batch exceeds bound")
    created = _checked_journal_file(journal_file)
    db = None
    try:
        db = _connect(journal_file)
        db.execute("BEGIN IMMEDIATE")
        digest = _scope_digest(scope, timezone_name)
        if created:
            if incoming is None:
                raise JournalError("Cannot export a journal that does not exist")
            db.execute("CREATE TABLE scope (id INTEGER PRIMARY KEY CHECK(id=1), "
                       "version INTEGER NOT NULL, digest TEXT NOT NULL)")
            db.execute("CREATE TABLE events (source_event_id TEXT PRIMARY KEY, "
                       "case_id TEXT NOT NULL, activity TEXT NOT NULL, timestamp TEXT NOT NULL)")
            db.execute("INSERT INTO scope (id,version,digest) VALUES (1,1,?)", (digest,))
        else:
            metadata = db.execute("SELECT version,digest FROM scope WHERE id=1").fetchone()
            if metadata != (1, digest):
                raise JournalError("Journal scope, selection or timezone mismatch")
        existing = db.execute(
            "SELECT case_id,activity,timestamp,source_event_id FROM events LIMIT ?",
            (MAX_EVENTS + 1,),
        ).fetchall()
        if len(existing) > MAX_EVENTS:
            raise JournalError("Journal exceeds event bound")
        by_id = {row[3]: tuple(row) for row in existing}
        if len(by_id) != len(existing):
            raise JournalError("Invalid journal duplicate revision")
        added = 0
        replayed = 0
        for row in incoming or []:
            if row[3] in by_id:
                if by_id[row[3]] != row:
                    raise JournalError("Conflicting selected-source revision on replay")
                replayed += 1
            else:
                if len(by_id) >= MAX_EVENTS:
                    raise JournalError("Journal event limit reached")
                db.execute(
                    "INSERT INTO events (case_id,activity,timestamp,source_event_id) "
                    "VALUES (?,?,?,?)", row,
                )
                by_id[row[3]] = row
                added += 1
        _check_grant(grant_file, scope)
        db.execute("COMMIT")
        if journal_file.stat().st_size > MAX_DB_BYTES:
            # A 4096-row bounded journal should never reach this threshold.
            raise JournalError("Journal file exceeds storage bound")
        return list(by_id.values()), added, replayed
    except (sqlite3.Error, OSError) as exc:
        raise JournalError("Journal transaction refused") from None
    finally:
        if db is not None:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()


def main(argv: list[str] | None = None, *, opener=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", action="append", required=True, dest="ids")
    parser.add_argument("--grant-file", type=Path, required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--since", help="Explicit inclusive provider-local bound; never auto-advanced")
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--consent-local-export", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.export_only:
            if not args.consent_local_export or args.consent_selected_read or args.since:
                raise JournalError("Explicit export-only consent required")
        elif not args.consent_selected_read or args.consent_local_export:
            raise JournalError("Explicit selected-source read consent required")
        scope = SelectedIncidents(
            host=args.instance, tenant_scope=args.tenant, sys_ids=tuple(args.ids),
            consented=True, grant_current=True,
        )
        locations = [os.path.abspath(p) for p in
                     (args.journal, args.output, args.grant_file)]
        if len(set(locations)) != 3:
            raise JournalError("Journal, grant and output must be separate files")
        _private_directory(args.output.parent)
        _check_grant(args.grant_file, scope)
        incoming = None
        represented = None
        if not args.export_only:
            staged = read_selected_once(
                scope, grant_file=args.grant_file,
                token=os.environ.get("OWG_SERVICENOW_TOKEN"),
                since=args.since, opener=opener,
            )
            csv_text, report = project_csv(scope, staged, timezone_name=args.timezone)
            incoming = _csv_rows(csv_text)
            represented = report["represented_incidents"]
        rows, added, replayed = update_journal(
            scope, timezone_name=args.timezone, journal_file=args.journal,
            grant_file=args.grant_file, incoming=incoming,
        )
        _publish_new(args.output, _export_csv(rows), grant_file=args.grant_file, scope=scope)
        print(json.dumps({
            "format": "openworkgraph.servicenow-selected-journal.v1",
            "mode": "export_only" if args.export_only else "selected_https_read",
            "selected_incidents": len(scope.sys_ids),
            "represented_in_latest_read": represented,
            "new_snapshots": added,
            "replayed_snapshots": replayed,
            "journal_snapshots": len(rows),
            "checkpoint_advanced": False,
            "historical_completeness": "unknown",
            "business_outcome_verified": False,
            "source_writes_performed": False,
            "automatic_owg_log_append": False,
            "journal_committed_before_csv_publication": True,
        }, sort_keys=True))
        return 0
    except (JournalError, SyncError, StagingError, sqlite3.Error, OSError,
            ValueError, OverflowError):
        print(json.dumps({"status": "refused", "reason":
                          "Check private journal/output, selected scope, consent, grant, "
                          "source response and new output path"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
