"""Offline ServiceNow checkpoint recovery from verified local OWG source events.

No provider request, local append, automatic retry, or source write.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
from urllib import error, request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .servicenow_incident_checkpoint import (
    CheckpointError, FUTURE_TOLERANCE, FORMAT as CHECKPOINT_FORMAT,
    _binding, _parse_utc, _publish_state, _read_state,
)
from .servicenow_incident_journal import JournalError, MAX_EVENTS, _private_directory
from .servicenow_incident_staging import SelectedIncidents, StagingError, _validate_updated
from .servicenow_incident_sync import SyncError, _check_grant, _source_local_to_utc
from .servicenow_owg_append import (
    AppendError, _LOG, _opener, _payload, _session, verify_local_target,
)

FORMAT = "openworkgraph.servicenow-checkpoint-reconciliation.v1"
MAX_EXPORT_BYTES = 2 * 1024 * 1024
_SOURCE_FIELDS = (
    "source_namespace", "source_event_id", "case_namespace", "case_id",
    "activity", "timestamp", "resource", "attributes",
)
_JOURNAL_FIELDS = ("case_id", "activity", "timestamp", "source_event_id")


class ReconcileError(ValueError):
    """No source keys, tenant labels, paths or credentials in user-visible errors."""


def _journal_rows(payload: bytes, expected_count: int) -> dict[str, tuple[str, str, datetime]]:
    try:
        reader = csv.reader(io.StringIO(payload.decode("utf-8"), newline=""))
        if tuple(next(reader)) != _JOURNAL_FIELDS:
            raise ReconcileError("Invalid private journal export")
        result = {}
        for row in reader:
            if len(row) != 4 or row[3] in result or len(result) >= MAX_EVENTS:
                raise ReconcileError("Invalid private journal export")
            result[row[3]] = (row[0], row[1], _parse_utc(row[2]))
    except (UnicodeError, csv.Error, StopIteration, ValueError, TypeError):
        raise ReconcileError("Invalid private journal export") from None
    if len(result) != expected_count or not result:
        raise ReconcileError("Invalid private journal export")
    return result


def _read_local_export(log_id: str, port: int, session: str, *, opener=None) -> bytes:
    url = (f"http://127.0.0.1:{port}/v1/event-logs/{log_id}/export"
           "?format=sourcecsv&case_type=source_case")
    req = request.Request(url, method="GET", headers={
        "Authorization": "OWG-Session " + session, "Accept": "text/csv",
    })
    transport = _opener() if opener is None else opener
    try:
        with transport.open(req, timeout=15) as response:
            status = response.status
            final_url = response.geturl()
            media = response.headers.get("Content-Type", "")
            body = response.read(MAX_EXPORT_BYTES + 1)
    except (OSError, error.URLError, ValueError):
        raise ReconcileError("Local source-event export unavailable") from None
    if (status != 200 or final_url != url
            or media.split(";", 1)[0].strip().lower() != "text/csv"
            or not body or len(body) > MAX_EXPORT_BYTES):
        raise ReconcileError("Local source-event export invalid or exceeds bound")
    return body


def _verify_local_export(
    payload: bytes, *, namespace: str,
    expected: dict[str, tuple[str, str, datetime]],
) -> datetime:
    """Require an exact local case/activity/time match for every journal key."""
    matched = set()
    seen = set()
    try:
        reader = csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
        if tuple(next(reader)) != _SOURCE_FIELDS:
            raise ReconcileError("Unsupported local source-event export schema")
        for row in reader:
            if len(row) != 8 or len(seen) >= MAX_EVENTS:
                raise ReconcileError("Local source-event export exceeds bound")
            source_ns, event_id, case_ns, case_id, activity, stamp, _, _ = row
            if (source_ns != namespace or case_ns != namespace
                    or not event_id or event_id in seen):
                raise ReconcileError("Local source-event namespace or identity mismatch")
            seen.add(event_id)
            if event_id in expected:
                if expected[event_id] != (case_id, activity, _parse_utc(stamp)):
                    raise ReconcileError("Local source-event payload differs from journal")
                matched.add(event_id)
    except (UnicodeError, csv.Error, StopIteration, ValueError, TypeError):
        raise ReconcileError("Invalid local source-event export") from None
    if matched != set(expected):
        raise ReconcileError("Not all journal snapshots are present in local OWG")
    return max(row[2] for row in expected.values())


def main(argv: list[str] | None = None, *, local_opener=None, now_fn=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", dest="ids", action="append", required=True)
    parser.add_argument("--grant-file", type=Path, required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--initial-since", help="Required if first-run checkpoint is absent")
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-local-journal-read", action="store_true")
    parser.add_argument("--consent-local-log-read", action="store_true")
    parser.add_argument("--consent-checkpoint-recovery", action="store_true")
    args = parser.parse_args(argv)

    lock = None
    publication_attempted = False
    try:
        if not all((args.consent_local_journal_read, args.consent_local_log_read,
                    args.consent_checkpoint_recovery)):
            raise ReconcileError("Three explicit offline recovery consents required")
        if not _LOG.fullmatch(args.log_id) or args.log_id != args.confirm_log_id:
            raise ReconcileError("Exact destination log confirmation required")
        if not 1 <= args.port <= 65535:
            raise ReconcileError("Invalid loopback port")
        session = _session(os.environ.get("OWG_DASHBOARD_SESSION"))
        scope = SelectedIncidents(host=args.instance, tenant_scope=args.tenant,
                                  sys_ids=tuple(args.ids), consented=True,
                                  grant_current=True)
        scope.require_authority()
        _private_directory(args.journal.parent)
        _private_directory(args.checkpoint.parent)
        lock_path = Path(str(args.checkpoint) + ".lock")
        locations = [os.path.abspath(path) for path in
                     (args.grant_file, args.journal, args.checkpoint, lock_path)]
        if len(set(locations)) != 4:
            raise ReconcileError("Private recovery paths must be distinct")
        try:
            lock = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                           getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError:
            raise ReconcileError("Concurrent or stale checkpoint lock") from None
        _check_grant(args.grant_file, scope)
        try:
            zone = ZoneInfo(args.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise ReconcileError("Invalid source timezone") from None
        binding = _binding(scope, args.timezone, args.log_id, args.journal)
        state = _read_state(args.checkpoint, binding)
        if state is None:
            if args.initial_since is None:
                raise ReconcileError("First-run lower bound required")
            _validate_updated(args.initial_since)
            _source_local_to_utc(args.initial_since, zone)
            state = {"format": CHECKPOINT_FORMAT, "binding": binding,
                     "initial_since": args.initial_since, "latest_source_utc": None}
        elif args.initial_since is not None and args.initial_since != state["initial_since"]:
            raise ReconcileError("Cannot change initial lower bound")
        now = datetime.now(timezone.utc) if now_fn is None else now_fn()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ReconcileError("Invalid clock")
        now = now.astimezone(timezone.utc)
        previous = (_parse_utc(state["latest_source_utc"])
                    if state["latest_source_utc"] is not None else None)
        if previous is not None and previous > now + FUTURE_TOLERANCE:
            raise ReconcileError("Future-dated stored checkpoint")
        body, count, namespace = _payload(scope, args.timezone,
                                          args.journal, args.grant_file)
        expected = _journal_rows(body, count)
        verify_local_target(args.log_id, args.port, namespace, session,
                            opener=local_opener)
        _check_grant(args.grant_file, scope)
        sourcecsv = _read_local_export(args.log_id, args.port, session,
                                       opener=local_opener)
        latest = _verify_local_export(sourcecsv, namespace=namespace,
                                      expected=expected)
        if latest > now + FUTURE_TOLERANCE:
            raise ReconcileError("Future-dated source observation")
        _check_grant(args.grant_file, scope)
        verify_local_target(args.log_id, args.port, namespace, session,
                            opener=local_opener)
        newest = max(value for value in (previous, latest) if value is not None)
        advanced = previous is None or newest > previous
        state["latest_source_utc"] = newest.isoformat()
        publication_attempted = True
        _publish_state(args.checkpoint, state, args.grant_file, scope)
        print(json.dumps({
            "format": FORMAT, "status": "reconciled",
            "verified_journal_snapshots": count,
            "checkpoint_committed": True, "checkpoint_advanced": advanced,
            "provider_read_performed": False, "local_owg_append_performed": False,
            "source_writes_performed": False, "historical_completeness": "unknown",
        }, sort_keys=True))
        return 0
    except (ReconcileError, CheckpointError, JournalError, SyncError,
            StagingError, AppendError, OSError, ValueError, OverflowError,
            TypeError, KeyError, csv.Error):
        print(json.dumps({
            "format": FORMAT,
            "status": "checkpoint_commit_unverified" if publication_attempted
                      else "not_reconciled",
            "checkpoint_committed": False if not publication_attempted else None,
            "provider_read_performed": False, "local_owg_append_performed": False,
            "source_writes_performed": False,
            "reason": "Inspect private checkpoint before retrying" if publication_attempted
                      else "Check consent, scope, grant, journal and exact local source events",
        }, sort_keys=True))
        return 3 if publication_attempted else 2
    finally:
        if lock is not None:
            os.close(lock)
            try:
                os.unlink(lock_path)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
