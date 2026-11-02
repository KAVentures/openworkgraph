"""Finite opt-in ServiceNow incremental checkpoint polling into local OWG.

Each poll independently completes the selected HTTPS read, private journal,
authenticated loopback append, and durable checkpoint before the next poll.
This is operator-invoked; it is not an unattended daemon or history feed.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .servicenow_incident_checkpoint import (
    CheckpointError, _binding, _read_state, _parse_utc, main as checkpoint_main,
    FUTURE_TOLERANCE,
)
from .servicenow_incident_journal import JournalError, _private_directory, _scope_digest
from .servicenow_incident_poll import (
    MAX_POLLS, MAX_WINDOW_SECONDS, MAX_INTERVAL_SECONDS, PollError,
    _paths, _wait_with_revocation_check,
)
from .servicenow_incident_staging import SelectedIncidents, StagingError, _validate_updated
from .servicenow_incident_sync import SyncError, _check_grant, _source_local_to_utc
from .servicenow_owg_append import AppendError, _LOG, _session, verify_local_target

FORMAT = "openworkgraph.servicenow-checkpoint-poll.v1"


class CheckpointPollError(ValueError):
    """Minimized refusal; no source IDs, paths or credentials in receipts."""


def _preflight(args, scope: SelectedIncidents, session: str, now: datetime) -> tuple[list[Path], str]:
    """Validate all local state and outputs before any ServiceNow GET."""
    scope.require_authority()
    _private_directory(args.checkpoint.parent)
    outputs = _paths(args, scope)
    lock = Path(str(args.checkpoint) + ".lock")
    reserved = {os.path.abspath(p) for p in
                (args.grant_file, args.journal, args.checkpoint, lock)}
    if len(reserved) != 4 or any(os.path.abspath(p) in reserved for p in outputs):
        raise CheckpointPollError("Private paths must be distinct")
    if lock.exists() or lock.is_symlink():
        raise CheckpointPollError("Concurrent or stale checkpoint lock")
    try:
        zone = ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise CheckpointPollError("Invalid declared source timezone") from None
    binding = _binding(scope, args.timezone, args.log_id, args.journal)
    state = _read_state(args.checkpoint, binding)
    if state is None:
        if args.initial_since is None:
            raise CheckpointPollError("Initial source lower bound required")
        _validate_updated(args.initial_since)
        _source_local_to_utc(args.initial_since, zone)
        initial = args.initial_since
    else:
        initial = state["initial_since"]
        if args.initial_since is not None and args.initial_since != initial:
            raise CheckpointPollError("Checkpoint initial bound cannot change")
        latest = state["latest_source_utc"]
        if latest is not None and _parse_utc(latest) > now + FUTURE_TOLERANCE:
            raise CheckpointPollError("Future-dated checkpoint")
    _check_grant(args.grant_file, scope)
    namespace = "snow_" + _scope_digest(scope, args.timezone)[:32]
    # Authenticated, read-only loopback check; never contact the source first
    # when the selected destination or checkpoint is already wrong.
    verify_local_target(args.log_id, args.port, namespace, session, opener=args._local_opener)
    _check_grant(args.grant_file, scope)
    return outputs, initial


def main(argv: list[str] | None = None, *, provider_opener=None,
         local_opener=None, sleep_fn=None, now_fn=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", action="append", required=True, dest="ids")
    parser.add_argument("--grant-file", required=True, type=Path)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--runs", required=True, type=int)
    parser.add_argument("--interval-seconds", required=True, type=int)
    parser.add_argument("--initial-since", help="Required for a new checkpoint only")
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--consent-bounded-poll", action="store_true")
    parser.add_argument("--consent-local-journal-read", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    parser.add_argument("--consent-incremental-checkpoint", action="store_true")
    args = parser.parse_args(argv)

    completed = 0
    added = 0
    duplicates = 0
    advances = 0
    possible_append = False
    recovery_may_exist = False
    try:
        if not all((args.consent_selected_read, args.consent_bounded_poll,
                    args.consent_local_journal_read, args.consent_local_append,
                    args.consent_incremental_checkpoint)):
            raise CheckpointPollError("Five explicit consents required")
        if type(args.runs) is not int or not 1 <= args.runs <= MAX_POLLS:
            raise CheckpointPollError("Poll count outside finite bound")
        if not 60 <= args.interval_seconds <= MAX_INTERVAL_SECONDS:
            raise CheckpointPollError("Poll interval outside bound")
        if (args.runs - 1) * args.interval_seconds > MAX_WINDOW_SECONDS:
            raise CheckpointPollError("Poll window exceeds two hours")
        if not _LOG.fullmatch(args.log_id) or args.log_id != args.confirm_log_id:
            raise CheckpointPollError("Exact destination log confirmation required")
        if not 1 <= args.port <= 65535:
            raise CheckpointPollError("Invalid local port")
        session = _session(os.environ.get("OWG_DASHBOARD_SESSION"))
        scope = SelectedIncidents(host=args.instance, tenant_scope=args.tenant,
                                  sys_ids=tuple(args.ids), consented=True,
                                  grant_current=True)
        current = datetime.now(timezone.utc) if now_fn is None else now_fn()
        if not isinstance(current, datetime) or current.tzinfo is None:
            raise CheckpointPollError("Invalid clock")
        current = current.astimezone(timezone.utc)
        # _preflight uses a private local opener; no ambient network is needed.
        args._local_opener = local_opener
        outputs, initial = _preflight(args, scope, session, current)
        sleeper = time.sleep if sleep_fn is None else sleep_fn

        for index, output in enumerate(outputs):
            if index:
                _wait_with_revocation_check(
                    args.interval_seconds, scope, args.grant_file, sleeper,
                )
            _check_grant(args.grant_file, scope)
            child = [
                "--instance", args.instance, "--tenant", args.tenant,
                "--grant-file", str(args.grant_file), "--timezone", args.timezone,
                "--journal", str(args.journal), "--checkpoint", str(args.checkpoint),
                "--output", str(output), "--log-id", args.log_id,
                "--confirm-log-id", args.confirm_log_id, "--port", str(args.port),
                "--consent-selected-read", "--consent-local-journal-read",
                "--consent-local-append", "--consent-incremental-checkpoint",
            ]
            for incident in args.ids:
                child.extend(("--incident-id", incident))
            if index == 0 and args.initial_since is not None:
                child.extend(("--initial-since", initial))
            capture = io.StringIO()
            # A child can have committed a local append even when its response
            # is lost. Do not retry or continue unless success is verified.
            possible_append = True
            with redirect_stdout(capture):
                code = checkpoint_main(
                    child, provider_opener=provider_opener,
                    local_opener=local_opener, now_fn=now_fn,
                )
            recovery_may_exist = output.exists()
            try:
                receipt = json.loads(capture.getvalue())
            except (TypeError, ValueError):
                raise CheckpointPollError("Unverifiable checkpoint receipt") from None
            if type(receipt) is not dict:
                raise CheckpointPollError("Unverifiable checkpoint receipt")
            if code != 0:
                if (code == 2 and receipt.get("status") == "refused"
                        and receipt.get("possible_local_append") is False):
                    possible_append = False
                raise CheckpointPollError("Incremental poll refused or uncertain")
            if (receipt.get("format") != "openworkgraph.servicenow-incremental-checkpoint.v1"
                    or receipt.get("status") != "completed"
                    or receipt.get("checkpoint_committed") is not True
                    or type(receipt.get("checkpoint_advanced")) is not bool
                    or receipt.get("local_owg_append_performed") is not True
                    or receipt.get("source_writes_performed") is not False
                    or receipt.get("historical_completeness") != "unknown"
                    or type(receipt.get("added_events")) is not int
                    or type(receipt.get("duplicate_events_skipped")) is not int
                    or receipt["added_events"] < 0
                    or receipt["duplicate_events_skipped"] < 0):
                raise CheckpointPollError("Unexpected checkpoint success receipt")
            state = _read_state(args.checkpoint,
                                _binding(scope, args.timezone, args.log_id, args.journal))
            if state is None or state["initial_since"] != initial:
                raise CheckpointPollError("Checkpoint publication not verified")
            _check_grant(args.grant_file, scope)
            possible_append = False
            completed += 1
            added += receipt["added_events"]
            duplicates += receipt["duplicate_events_skipped"]
            advances += int(receipt["checkpoint_advanced"])
        print(json.dumps({
            "format": FORMAT, "status": "completed", "completed_polls": completed,
            "checkpoint_commits": completed, "checkpoint_advances": advances,
            "recovery_csv_exports_created": completed, "added_events": added,
            "duplicate_events_skipped": duplicates,
            "historical_completeness": "unknown", "source_writes_performed": False,
            "local_owg_append_performed": True, "unattended_scheduling": False,
        }, sort_keys=True))
        return 0
    except (CheckpointPollError, CheckpointError, PollError, JournalError,
            AppendError, SyncError, StagingError, OSError, ValueError,
            OverflowError, TypeError, KeyError):
        print(json.dumps({
            "format": FORMAT,
            "status": "append_or_checkpoint_unverified" if possible_append else "refused",
            "completed_polls": completed, "possible_local_append": possible_append,
            "recovery_csv_may_exist": recovery_may_exist,
            "reason": "Inspect local OWG log, journal and checkpoint before retrying"
                      if possible_append else "Check consent, grant, scope and private paths",
        }, sort_keys=True))
        return 3 if possible_append else 2


if __name__ == "__main__":
    sys.exit(main())
