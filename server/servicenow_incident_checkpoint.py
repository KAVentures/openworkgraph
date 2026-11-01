"""Operator-invoked ServiceNow incremental selected-read -> journal -> OWG append.

Checkpoint advances only after a verified authenticated local append. The
48-hour overlap is a replay aid, NOT a complete ServiceNow change-feed cursor.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .servicenow_incident_journal import (
    JournalError, MAX_EVENTS, _checked_journal_file, _connect,
    _private_directory, _scope_digest,
)
from .servicenow_incident_run import main as run_main
from .servicenow_incident_staging import SelectedIncidents, StagingError, _validate_updated
from .servicenow_incident_sync import SyncError, _check_grant, _source_local_to_utc
from .servicenow_owg_append import AppendError, _LOG

FORMAT = "openworkgraph.servicenow-incremental-checkpoint.v1"
OVERLAP = timedelta(hours=48)
FUTURE_TOLERANCE = timedelta(minutes=5)
_STATE_KEYS = frozenset({"format", "binding", "initial_since", "latest_source_utc"})


class CheckpointError(ValueError):
    """Private local checkpoint refusal; never expose source identifiers."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointError("Duplicate checkpoint attribute")
        result[key] = value
    return result


def _binding(scope: SelectedIncidents, timezone_name: str,
             log_id: str, journal: Path) -> str:
    value = [_scope_digest(scope, timezone_name), log_id, os.path.abspath(journal)]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def _parse_utc(value: str) -> datetime:
    if type(value) is not str or len(value) > 40:
        raise CheckpointError("Invalid source observation timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise CheckpointError("Invalid source observation timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CheckpointError("Source observation must be UTC")
    return parsed.astimezone(timezone.utc)


def _read_state(path: Path, binding: str) -> dict | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size > 4096
                    or (os.name == "posix" and
                        (info.st_uid != os.getuid() or info.st_mode & 0o077))):
                raise CheckpointError("Unsafe checkpoint file")
            raw = handle.read(4097)
        if len(raw) > 4096:
            raise CheckpointError("Oversized checkpoint file")
        state = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise CheckpointError("Invalid or unavailable private checkpoint") from None
    if (type(state) is not dict or set(state) != _STATE_KEYS
            or state["format"] != FORMAT or state["binding"] != binding
            or type(state["initial_since"]) is not str
            or (state["latest_source_utc"] is not None and
                type(state["latest_source_utc"]) is not str)):
        raise CheckpointError("Checkpoint scope, log or format mismatch")
    _validate_updated(state["initial_since"])
    if state["latest_source_utc"] is not None:
        _parse_utc(state["latest_source_utc"])
    return state


def _journal_latest(scope: SelectedIncidents, timezone_name: str,
                    journal: Path) -> datetime | None:
    _checked_journal_file(journal)
    db = None
    try:
        db = _connect(journal)
        db.execute("PRAGMA query_only=ON")
        metadata = db.execute("SELECT version,digest FROM scope WHERE id=1").fetchone()
        if metadata != (1, _scope_digest(scope, timezone_name)):
            raise CheckpointError("Journal selection mismatch")
        count, latest = db.execute("SELECT COUNT(*), MAX(timestamp) FROM events").fetchone()
        if type(count) is not int or not 0 <= count <= MAX_EVENTS:
            raise CheckpointError("Journal exceeds bound")
        return _parse_utc(latest) if latest is not None else None
    except (sqlite3.Error, OSError):
        raise CheckpointError("Could not read scoped journal") from None
    finally:
        if db is not None:
            db.close()


def _since(state: dict, zone: ZoneInfo) -> str:
    initial = state["initial_since"]
    if state["latest_source_utc"] is None:
        return initial
    utc = _parse_utc(state["latest_source_utc"])
    local = (utc - OVERLAP).astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")
    return max(initial, local)


def _publish_state(path: Path, state: dict, grant_file: Path,
                   scope: SelectedIncidents) -> None:
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".owg-snow-checkpoint-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(state, separators=(",", ":"), sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        _check_grant(grant_file, scope)
        os.replace(temporary, path)
        temporary = None
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except (OSError, ValueError):
        raise CheckpointError("Could not commit private checkpoint") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def main(argv: list[str] | None = None, *, provider_opener=None,
         local_opener=None, now_fn=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", action="append", dest="ids", required=True)
    parser.add_argument("--grant-file", type=Path, required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--initial-since", help="Required on first run; provider-local timestamp")
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--consent-local-journal-read", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    parser.add_argument("--consent-incremental-checkpoint", action="store_true")
    args = parser.parse_args(argv)

    lock = None
    append_completed = False
    possible_append = False
    try:
        if not all((args.consent_selected_read, args.consent_local_journal_read,
                    args.consent_local_append, args.consent_incremental_checkpoint)):
            raise CheckpointError("Four explicit consents required")
        if not _LOG.fullmatch(args.log_id) or args.log_id != args.confirm_log_id:
            raise CheckpointError("Exact local log confirmation required")
        if not 1 <= args.port <= 65535:
            raise CheckpointError("Invalid loopback port")
        scope = SelectedIncidents(host=args.instance, tenant_scope=args.tenant,
                                  sys_ids=tuple(args.ids), consented=True,
                                  grant_current=True)
        scope.require_authority()
        _private_directory(args.checkpoint.parent)
        _private_directory(args.journal.parent)
        _private_directory(args.output.parent)
        paths = [os.path.abspath(p) for p in
                 (args.grant_file, args.journal, args.checkpoint, args.output)]
        lock_path = Path(str(args.checkpoint) + ".lock")
        if len(set(paths + [os.path.abspath(lock_path)])) != 5:
            raise CheckpointError("All private paths must be distinct")
        if args.output.exists() or args.output.is_symlink():
            raise CheckpointError("Recovery CSV already exists")
        try:
            lock = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                           getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError:
            raise CheckpointError("Concurrent or stale checkpoint lock") from None
        _check_grant(args.grant_file, scope)
        try:
            zone = ZoneInfo(args.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise CheckpointError("Invalid source timezone") from None
        binding = _binding(scope, args.timezone, args.log_id, args.journal)
        state = _read_state(args.checkpoint, binding)
        if state is None:
            if args.initial_since is None:
                raise CheckpointError("Initial lower bound required")
            _validate_updated(args.initial_since)
            _source_local_to_utc(args.initial_since, zone)
            state = {"format": FORMAT, "binding": binding,
                     "initial_since": args.initial_since, "latest_source_utc": None}
        elif args.initial_since is not None and args.initial_since != state["initial_since"]:
            raise CheckpointError("Cannot change checkpoint initial lower bound")
        current = (datetime.now(timezone.utc) if now_fn is None else now_fn())
        if not isinstance(current, datetime) or current.tzinfo is None:
            raise CheckpointError("Invalid clock")
        current = current.astimezone(timezone.utc)
        if (state["latest_source_utc"] is not None and
                _parse_utc(state["latest_source_utc"]) > current + FUTURE_TOLERANCE):
            raise CheckpointError("Future-dated stored checkpoint")
        since = _since(state, zone)
        child = [
            "--instance", args.instance, "--tenant", args.tenant,
            "--grant-file", str(args.grant_file), "--timezone", args.timezone,
            "--journal", str(args.journal), "--output", str(args.output),
            "--since", since, "--log-id", args.log_id,
            "--confirm-log-id", args.confirm_log_id, "--port", str(args.port),
            "--consent-selected-read", "--consent-local-journal-read",
            "--consent-local-append",
        ]
        for incident in args.ids:
            child.extend(("--incident-id", incident))
        capture = io.StringIO()
        possible_append = True
        with redirect_stdout(capture):
            code = run_main(child, provider_opener=provider_opener,
                            local_opener=local_opener)
        try:
            report = json.loads(capture.getvalue())
        except (ValueError, TypeError):
            raise CheckpointError("Unverifiable child receipt") from None
        if type(report) is not dict:
            raise CheckpointError("Unverifiable child receipt")
        if code != 0:
            possible_append = report.get("possible_local_append") is not False
            raise CheckpointError("Source or local append refused")
        if (report.get("format") != "openworkgraph.servicenow-selected-run.v1"
                or report.get("status") != "completed"
                or report.get("local_owg_append_performed") is not True
                or report.get("source_writes_performed") is not False
                or report.get("checkpoint_advanced") is not False
                or report.get("historical_completeness") != "unknown"
                or report.get("rejected_events") != 0):
            raise CheckpointError("Unexpected child success receipt")
        append_completed = True
        possible_append = False
        _check_grant(args.grant_file, scope)
        latest = _journal_latest(scope, args.timezone, args.journal)
        previous = (_parse_utc(state["latest_source_utc"])
                    if state["latest_source_utc"] is not None else None)
        newest = max((v for v in (latest, previous) if v is not None), default=None)
        if newest is not None and newest > current + FUTURE_TOLERANCE:
            raise CheckpointError("Future-dated source observation")
        advanced = newest is not None and (previous is None or newest > previous)
        state["latest_source_utc"] = newest.isoformat() if newest else None
        _publish_state(args.checkpoint, state, args.grant_file, scope)
        print(json.dumps({"format": FORMAT, "status": "completed",
                          "local_owg_append_performed": True,
                          "checkpoint_committed": True, "checkpoint_advanced": advanced,
                          "overlap_hours": 48, "historical_completeness": "unknown",
                          "source_writes_performed": False,
                          "added_events": report["added_events"],
                          "duplicate_events_skipped": report["duplicate_events_skipped"]},
                         sort_keys=True))
        return 0
    except (CheckpointError, JournalError, SyncError, StagingError, AppendError,
            OSError, ValueError, OverflowError, sqlite3.Error, TypeError, KeyError):
        status = ("append_completed_checkpoint_unverified" if append_completed
                  else "append_unverified" if possible_append else "refused")
        print(json.dumps({"format": FORMAT, "status": status,
                          "checkpoint_committed": False,
                          "possible_local_append": possible_append or append_completed,
                          "reason": "Inspect local log and checkpoint before retrying"
                                    if possible_append or append_completed else
                                    "Check consent, scope, grant and private paths"},
                         sort_keys=True))
        return 3 if possible_append or append_completed else 2
    finally:
        if lock is not None:
            os.close(lock)
            try:
                os.unlink(lock_path)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
