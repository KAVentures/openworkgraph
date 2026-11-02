"""Finite, explicitly consented ServiceNow selected-incident polling into an OWG CSV journal.

Operator-invoked only. No daemon, automatic checkpoint, provider write or OWG
workspace append. See docs/SERVICENOW_INCIDENT_POLL.md.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import sys
import time

from .servicenow_incident_staging import SelectedIncidents, StagingError
from .servicenow_incident_sync import SyncError, _check_grant
from .servicenow_incident_journal import (
    JournalError, _private_directory, main as journal_main,
    verify_existing_scope,
)

_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}\Z", re.ASCII)
MAX_POLLS = 8
MAX_WINDOW_SECONDS = 7200
MAX_INTERVAL_SECONDS = 1800
RECHECK_SECONDS = 5


class PollError(ValueError):
    """Local refusal without echoing credentials, paths or source records."""


def _paths(args, scope: SelectedIncidents) -> list[Path]:
    _private_directory(args.output_dir)
    _private_directory(args.journal.parent)
    if args.journal.is_symlink():
        raise PollError("Journal symlink refused")
    if not _PREFIX.fullmatch(args.output_prefix):
        raise PollError("Invalid output prefix")
    outputs = [
        args.output_dir / f"{args.output_prefix}-{index:03d}.csv"
        for index in range(1, args.runs + 1)
    ]
    reserved = {os.path.abspath(args.grant_file), os.path.abspath(args.journal)}
    if any(os.path.abspath(p) in reserved or p.exists() or p.is_symlink()
           for p in outputs):
        raise PollError("Output files must be new and distinct")
    _check_grant(args.grant_file, scope)
    verify_existing_scope(
        scope, timezone_name=args.timezone, journal_file=args.journal,
    )
    return outputs


def _wait_with_revocation_check(seconds: int, scope: SelectedIncidents,
                                grant_file: Path, sleep_fn) -> None:
    # A revoked local grant stops further reads within at most five seconds
    # of scheduled waiting, subject to OS scheduling. It is not a provider
    # revocation fence or a guarantee about in-flight HTTP requests.
    remaining = seconds
    while remaining:
        _check_grant(grant_file, scope)
        duration = min(remaining, RECHECK_SECONDS)
        sleep_fn(duration)
        remaining -= duration
        _check_grant(grant_file, scope)


def main(argv: list[str] | None = None, *, opener=None, sleep_fn=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", action="append", required=True, dest="ids")
    parser.add_argument("--grant-file", required=True, type=Path)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--runs", required=True, type=int)
    parser.add_argument("--interval-seconds", required=True, type=int)
    parser.add_argument("--since", help="Operator-supplied fixed inclusive lower bound")
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--consent-bounded-poll", action="store_true")
    args = parser.parse_args(argv)

    completed = 0
    new_snapshots = 0
    replayed_snapshots = 0
    journal_snapshots = 0
    try:
        if not args.consent_selected_read or not args.consent_bounded_poll:
            raise PollError("Explicit source and bounded-poll consent required")
        if not 1 <= args.runs <= MAX_POLLS:
            raise PollError("Poll count exceeds finite bound")
        if not 60 <= args.interval_seconds <= MAX_INTERVAL_SECONDS:
            raise PollError("Poll interval outside supported bound")
        if (args.runs - 1) * args.interval_seconds > MAX_WINDOW_SECONDS:
            raise PollError("Poll window exceeds two hours")
        scope = SelectedIncidents(
            host=args.instance, tenant_scope=args.tenant, sys_ids=tuple(args.ids),
            consented=True, grant_current=True,
        )
        scope.require_authority()
        outputs = _paths(args, scope)
        sleeper = time.sleep if sleep_fn is None else sleep_fn
        for index, output in enumerate(outputs):
            if index:
                _wait_with_revocation_check(
                    args.interval_seconds, scope, args.grant_file, sleeper,
                )
            _check_grant(args.grant_file, scope)
            child_args = [
                "--instance", args.instance, "--tenant", args.tenant,
                "--grant-file", str(args.grant_file), "--timezone", args.timezone,
                "--journal", str(args.journal), "--output", str(output),
                "--consent-selected-read",
            ]
            for incident in args.ids:
                child_args += ["--incident-id", incident]
            if args.since is not None:
                child_args += ["--since", args.since]
            capture = io.StringIO()
            with redirect_stdout(capture):
                result = journal_main(child_args, opener=opener)
            if result != 0:
                raise PollError("Selected-source poll or journal publication refused")
            report = json.loads(capture.getvalue())
            if (report.get("mode") != "selected_https_read"
                    or report.get("source_writes_performed") is not False
                    or report.get("checkpoint_advanced") is not False
                    or report.get("automatic_owg_log_append") is not False):
                raise PollError("Unexpected selected-source journal report")
            completed += 1
            new_snapshots += report["new_snapshots"]
            replayed_snapshots += report["replayed_snapshots"]
            journal_snapshots = report["journal_snapshots"]
        print(json.dumps({
            "format": "openworkgraph.servicenow-bounded-poll.v1",
            "status": "completed",
            "completed_polls": completed,
            "new_snapshots": new_snapshots,
            "replayed_snapshots": replayed_snapshots,
            "journal_snapshots": journal_snapshots,
            "csv_exports_created": completed,
            "historical_completeness": "unknown",
            "checkpoint_advanced": False,
            "source_writes_performed": False,
            "automatic_owg_log_append": False,
        }, sort_keys=True))
        return 0
    except (PollError, JournalError, SyncError, StagingError, OSError,
            ValueError, OverflowError, TypeError, KeyError):
        print(json.dumps({
            "status": "refused",
            "completed_polls": completed,
            "reason": "Check selected scope, consent, private paths, grant and source response",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
