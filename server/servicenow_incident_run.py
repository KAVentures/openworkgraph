"""One explicitly authorized ServiceNow read -> durable journal -> local OWG append.

This is an operator-invoked, bounded single run, not a daemon or provider
history feed. No ServiceNow writes or implicit OWG log creation.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys

from .servicenow_incident_journal import (
    JournalError, _private_directory, _scope_digest, verify_existing_scope,
    main as journal_main,
)
from .servicenow_incident_staging import SelectedIncidents, StagingError
from .servicenow_incident_sync import SyncError, _check_grant
from .servicenow_owg_append import (
    AppendError, _LOG, _session, verify_local_target, main as append_main,
)


class RunError(ValueError):
    """A refusal with no source, path, session or token disclosure."""


def _child(main_fn, argv, **kwargs):
    output = io.StringIO()
    with redirect_stdout(output):
        code = main_fn(argv, **kwargs)
    try:
        report = json.loads(output.getvalue())
    except (ValueError, TypeError):
        raise RunError("Invalid component receipt") from None
    if type(report) is not dict:
        raise RunError("Invalid component receipt")
    return code, report


def main(argv: list[str] | None = None, *, provider_opener=None,
         local_opener=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", dest="ids", action="append", required=True)
    parser.add_argument("--grant-file", required=True, type=Path)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="New private recovery CSV; never overwritten")
    parser.add_argument("--since", help="Explicit provider-local inclusive lower bound")
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--consent-local-journal-read", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    args = parser.parse_args(argv)

    journal_done = False
    possible_append = False
    try:
        if not (args.consent_selected_read and args.consent_local_journal_read
                and args.consent_local_append):
            raise RunError("All three independent consents required")
        if not _LOG.fullmatch(args.log_id) or args.confirm_log_id != args.log_id:
            raise RunError("Exact destination log confirmation required")
        if not 1 <= args.port <= 65535:
            raise RunError("Invalid loopback port")
        session = _session(os.environ.get("OWG_DASHBOARD_SESSION"))
        scope = SelectedIncidents(
            host=args.instance, tenant_scope=args.tenant,
            sys_ids=tuple(args.ids), consented=True, grant_current=True,
        )
        scope.require_authority()
        _private_directory(args.output.parent)
        _private_directory(args.journal.parent)
        if (args.output.exists() or args.output.is_symlink()
                or len({os.path.abspath(args.output), os.path.abspath(args.journal),
                        os.path.abspath(args.grant_file)}) != 3):
            raise RunError("Recovery CSV must be new and distinct")
        _check_grant(args.grant_file, scope)
        verify_existing_scope(
            scope, timezone_name=args.timezone, journal_file=args.journal,
        )
        namespace = "snow_" + _scope_digest(scope, args.timezone)[:32]
        # Verify the actual authenticated OWG destination BEFORE contacting
        # ServiceNow, so a wrong tenant/log cannot trigger even a source read.
        verify_local_target(
            args.log_id, args.port, namespace, session, opener=local_opener,
        )
        _check_grant(args.grant_file, scope)
        shared = [
            "--instance", args.instance, "--tenant", args.tenant,
            "--grant-file", str(args.grant_file),
            "--timezone", args.timezone, "--journal", str(args.journal),
        ]
        for incident in args.ids:
            shared.extend(("--incident-id", incident))
        journal_args = shared + [
            "--output", str(args.output), "--consent-selected-read",
        ]
        if args.since is not None:
            journal_args.extend(("--since", args.since))
        source_code, source_report = _child(
            journal_main, journal_args, opener=provider_opener,
        )
        if source_code != 0:
            raise RunError("Selected read or durable journal refused")
        if (source_report.get("mode") != "selected_https_read"
                or source_report.get("source_writes_performed") is not False
                or source_report.get("automatic_owg_log_append") is not False
                or source_report.get("checkpoint_advanced") is not False
                or type(source_report.get("journal_snapshots")) is not int):
            raise RunError("Unexpected source journal receipt")
        journal_done = True
        _check_grant(args.grant_file, scope)
        append_args = shared + [
            "--log-id", args.log_id, "--confirm-log-id", args.confirm_log_id,
            "--port", str(args.port), "--consent-local-journal-read",
            "--consent-local-append",
        ]
        # Once control enters the append component, an exception or malformed
        # receipt cannot prove the POST did not happen. Default to uncertain.
        possible_append = True
        append_code, append_report = _child(
            append_main, append_args, opener=local_opener,
        )
        if append_code != 0:
            possible_append = (append_report.get("possible_local_append") is not False
                               or append_report.get("local_owg_append_performed") is True)
            print(json.dumps({
                "format": "openworkgraph.servicenow-selected-run.v1",
                "status": "append_unverified" if possible_append else "append_refused",
                "journal_committed": True,
                "recovery_csv_created": True,
                "possible_local_append": possible_append,
                "source_writes_performed": False,
                "historical_completeness": "unknown",
                "next_step": "Inspect local append history before any retry"
                             if possible_append else "Review consent, grant and local log",
            }, sort_keys=True))
            return 3 if possible_append else 2
        if (append_report.get("status") != "completed"
                or append_report.get("provider_read_performed") is not False
                or append_report.get("source_writes_performed") is not False
                or append_report.get("local_owg_append_performed") is not True
                or append_report.get("journal_snapshots") !=
                   source_report["journal_snapshots"]):
            possible_append = True
            raise RunError("Unexpected local append receipt")
        print(json.dumps({
            "format": "openworkgraph.servicenow-selected-run.v1",
            "status": "completed",
            "journal_committed": True,
            "recovery_csv_created": True,
            "journal_snapshots": source_report["journal_snapshots"],
            "new_snapshots": source_report["new_snapshots"],
            "replayed_snapshots": source_report["replayed_snapshots"],
            "added_events": append_report["added_events"],
            "duplicate_events_skipped": append_report["duplicate_events_skipped"],
            "rejected_events": append_report["rejected_events"],
            "local_owg_append_performed": True,
            "source_writes_performed": False,
            "historical_completeness": "unknown",
            "checkpoint_advanced": False,
        }, sort_keys=True))
        return 0
    except (RunError, AppendError, JournalError, SyncError, StagingError,
            OSError, ValueError, OverflowError, TypeError, KeyError):
        print(json.dumps({
            "format": "openworkgraph.servicenow-selected-run.v1",
            "status": "append_unverified" if possible_append else "refused",
            "journal_may_have_changed": journal_done,
            "possible_local_append": possible_append,
            "reason": "Inspect local append history before retrying"
                      if possible_append else
                      "Check consent, scope, grant, private paths, source and local target",
        }, sort_keys=True))
        return 3 if possible_append else 2


if __name__ == "__main__":
    sys.exit(main())
