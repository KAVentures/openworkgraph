"""Finite, explicitly authorized ServiceNow polling with per-poll local OWG append.

Operator-invoked only. Every poll runs a selected HTTPS read, a durable private
journal/recovery CSV, then an authenticated local event-log append. A failed or
uncertain append stops the sequence; no automatic retry or source write.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import time

from .servicenow_incident_journal import JournalError, _scope_digest
from .servicenow_incident_poll import (
    MAX_INTERVAL_SECONDS, MAX_POLLS, MAX_WINDOW_SECONDS, PollError,
    _paths, _wait_with_revocation_check,
)
from .servicenow_incident_run import main as run_main
from .servicenow_incident_staging import SelectedIncidents, StagingError
from .servicenow_incident_sync import SyncError, _check_grant
from .servicenow_owg_append import AppendError, _LOG, _session, verify_local_target


class PollAppendError(ValueError):
    """Generic refusal that never includes source identifiers or credentials."""


def main(argv: list[str] | None = None, *, provider_opener=None,
         local_opener=None, sleep_fn=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", action="append", dest="ids", required=True)
    parser.add_argument("--grant-file", type=Path, required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--runs", type=int, required=True)
    parser.add_argument("--interval-seconds", type=int, required=True)
    parser.add_argument("--since", help="Fixed operator-selected inclusive bound; no cursor")
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--consent-bounded-poll", action="store_true")
    parser.add_argument("--consent-local-journal-read", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    args = parser.parse_args(argv)

    completed = 0
    new_snapshots = 0
    replayed_snapshots = 0
    added_events = 0
    duplicate_events = 0
    journal_snapshots = 0
    possible_append = False
    recovery_may_exist = False
    try:
        if not (args.consent_selected_read and args.consent_bounded_poll
                and args.consent_local_journal_read and args.consent_local_append):
            raise PollAppendError("Four independent consents required")
        if not 1 <= args.runs <= MAX_POLLS:
            raise PollAppendError("Poll count outside bound")
        if not 60 <= args.interval_seconds <= MAX_INTERVAL_SECONDS:
            raise PollAppendError("Poll interval outside bound")
        if (args.runs - 1) * args.interval_seconds > MAX_WINDOW_SECONDS:
            raise PollAppendError("Poll window outside bound")
        if not _LOG.fullmatch(args.log_id) or args.confirm_log_id != args.log_id:
            raise PollAppendError("Exact destination confirmation required")
        if not 1 <= args.port <= 65535:
            raise PollAppendError("Invalid local port")
        session = _session(os.environ.get("OWG_DASHBOARD_SESSION"))
        scope = SelectedIncidents(
            host=args.instance, tenant_scope=args.tenant,
            sys_ids=tuple(args.ids), consented=True, grant_current=True,
        )
        scope.require_authority()
        # Validate every output, private journal and scope before any provider
        # request. Existing files are never overwritten, even on later polls.
        outputs = _paths(args, scope)
        namespace = "snow_" + _scope_digest(scope, args.timezone)[:32]
        verify_local_target(
            args.log_id, args.port, namespace, session, opener=local_opener,
        )
        _check_grant(args.grant_file, scope)
        sleeper = time.sleep if sleep_fn is None else sleep_fn
        for index, output in enumerate(outputs):
            if index:
                _wait_with_revocation_check(
                    args.interval_seconds, scope, args.grant_file, sleeper,
                )
            _check_grant(args.grant_file, scope)
            child = [
                "--instance", args.instance, "--tenant", args.tenant,
                "--grant-file", str(args.grant_file),
                "--timezone", args.timezone,
                "--journal", str(args.journal), "--output", str(output),
                "--log-id", args.log_id, "--confirm-log-id", args.confirm_log_id,
                "--port", str(args.port),
                "--consent-selected-read", "--consent-local-journal-read",
                "--consent-local-append",
            ]
            for incident in args.ids:
                child.extend(("--incident-id", incident))
            if args.since is not None:
                child.extend(("--since", args.since))
            capture = io.StringIO()
            # Once control enters the child, it may have posted to local OWG.
            # Only a validated child receipt can prove no append was attempted.
            possible_append = True
            with redirect_stdout(capture):
                code = run_main(
                    child, provider_opener=provider_opener,
                    local_opener=local_opener,
                )
            try:
                report = json.loads(capture.getvalue())
            except (ValueError, TypeError):
                raise PollAppendError("Unverifiable child receipt") from None
            if type(report) is not dict:
                raise PollAppendError("Unverifiable child receipt")
            recovery_may_exist = output.exists()
            if code != 0:
                if (code == 2 and report.get("status") == "refused"
                        and report.get("possible_local_append") is False):
                    possible_append = False
                elif (code == 2 and report.get("status") == "append_refused"
                      and report.get("possible_local_append") is False):
                    possible_append = False
                raise PollAppendError("Selected poll or local append refused")
            if (report.get("format") != "openworkgraph.servicenow-selected-run.v1"
                    or report.get("status") != "completed"
                    or report.get("local_owg_append_performed") is not True
                    or report.get("source_writes_performed") is not False
                    or report.get("checkpoint_advanced") is not False
                    or report.get("historical_completeness") != "unknown"
                    or report.get("rejected_events") != 0
                    or any(type(report.get(key)) is not int or report[key] < 0
                           for key in ("new_snapshots", "replayed_snapshots",
                                       "journal_snapshots", "added_events",
                                       "duplicate_events_skipped"))):
                raise PollAppendError("Unexpected poll/append receipt")
            possible_append = False
            completed += 1
            new_snapshots += report["new_snapshots"]
            replayed_snapshots += report["replayed_snapshots"]
            added_events += report["added_events"]
            duplicate_events += report["duplicate_events_skipped"]
            journal_snapshots = report["journal_snapshots"]
        print(json.dumps({
            "format": "openworkgraph.servicenow-poll-append.v1",
            "status": "completed", "completed_polls": completed,
            "new_snapshots": new_snapshots,
            "replayed_snapshots": replayed_snapshots,
            "journal_snapshots": journal_snapshots,
            "added_events": added_events,
            "duplicate_events_skipped": duplicate_events,
            "recovery_csv_exports_created": completed,
            "local_owg_append_performed": True,
            "source_writes_performed": False,
            "historical_completeness": "unknown",
            "checkpoint_advanced": False,
        }, sort_keys=True))
        return 0
    except (PollAppendError, PollError, JournalError, AppendError,
            SyncError, StagingError, OSError, ValueError, OverflowError,
            TypeError, KeyError):
        print(json.dumps({
            "format": "openworkgraph.servicenow-poll-append.v1",
            "status": "append_unverified" if possible_append else "refused",
            "completed_polls": completed,
            "possible_local_append": possible_append,
            "recovery_csv_may_exist": recovery_may_exist,
            "reason": "Inspect local append history before retrying"
                      if possible_append else
                      "Check consent, grant, scope, local target and private outputs",
        }, sort_keys=True))
        return 3 if possible_append else 2


if __name__ == "__main__":
    sys.exit(main())
