"""One explicitly consented ServiceNow selected read -> durable journal -> OWG append.

This is a one-shot operator command, not a scheduled connector or a source
history feed. No checkpoint is advanced and no provider write is performed.
An uncertain OWG POST is never retried automatically.
"""
from __future__ import annotations

from .connector_oauth import connector_token as _connector_token
import argparse
import json
import os
from pathlib import Path
import sys

from .servicenow_incident_journal import (
    JournalError, _csv_rows, _private_directory, update_journal,
    verify_existing_scope,
)
from .servicenow_incident_staging import SelectedIncidents, StagingError
from .servicenow_incident_sync import (
    SyncError, _check_grant, project_csv, read_selected_once,
)
from .servicenow_owg_append import (
    AppendError, _LOG, _opener, _payload, _session, append_local,
)
from .servicenow_owg_bootstrap import (
    BootstrapError, _inventory, _same_scope,
)

FORMAT = "openworkgraph.servicenow-selected-incremental.v1"


class IncrementalError(ValueError):
    """A generic refusal; never include source values or credentials."""


class UncertainIncremental(IncrementalError):
    """A local append may have committed; manual history review is required."""


def _target(logs: list[dict], log_id: str, namespace: str) -> int:
    matches = [row for row in logs if row["log_id"] == log_id]
    if len(matches) != 1:
        raise IncrementalError("Selected OWG log missing")
    if any(row["log_id"] != log_id and _same_scope(row, namespace) for row in logs):
        raise IncrementalError("Ambiguous duplicate source namespace")
    row = matches[0]
    identity = row.get("source_identity")
    namespaces = identity.get("namespaces") if type(identity) is dict else None
    if (type(namespaces) is not list or len(namespaces) != 1
            or type(namespaces[0]) is not dict
            or namespaces[0].get("source_namespace") != namespace
            or namespaces[0].get("case_namespace") != namespace
            or row.get("source_format") != "csv"
            or type(row.get("events")) is not int or row["events"] < 0):
        raise IncrementalError("OWG target source scope or counts mismatch")
    return row["events"]


def sync_selected(*, instance: str, tenant: str, incident_ids: list[str],
                  grant_file: Path, timezone_name: str, journal: Path,
                  log_id: str, confirm_log_id: str, port: int,
                  consent_source_read: bool = False,
                  consent_journal_update: bool = False,
                  consent_local_append: bool = False,
                  since: str | None = None, token: str | None = None,
                  session: str | None = None, provider_opener=None, opener=None):
    """Append a selected new revision through real ServiceNow and OWG paths."""
    if (consent_source_read is not True or consent_journal_update is not True
            or consent_local_append is not True):
        raise IncrementalError("Three independent consents required")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise IncrementalError("Exact existing log confirmation required")
    session = _session(session)
    scope = SelectedIncidents(host=instance, tenant_scope=tenant,
                              sys_ids=tuple(incident_ids),
                              consented=True, grant_current=True)
    scope.require_authority()
    _private_directory(journal.parent)
    # This workflow is only for a reviewed journal that already has an OWG log.
    # It does not create a journal or a new log on an unreviewed source.
    verify_existing_scope(scope, timezone_name=timezone_name, journal_file=journal)
    lock = journal.with_name(journal.name + ".incremental.lock")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock, flags, 0o600)
        os.close(fd)
    except OSError:
        raise IncrementalError("Concurrent or unsafe selected sync") from None
    attempted = False
    try:
        transport = _opener() if opener is None else opener
        # Validate all existing journal rows, grant, target session and scope
        # BEFORE any provider network access. No accidental first-time import.
        _, _, namespace = _payload(scope, timezone_name, journal, grant_file)
        before = _inventory(transport, port, session)
        _target(before, log_id, namespace)
        _check_grant(grant_file, scope)
        staged = read_selected_once(
            scope, grant_file=grant_file, token=token,
            since=since, opener=provider_opener,
        )
        csv_text, _ = project_csv(scope, staged, timezone_name=timezone_name)
        incoming = _csv_rows(csv_text)
        _check_grant(grant_file, scope)
        if not incoming:
            return {
                "format": FORMAT, "status": "no_selected_snapshots",
                "provider_read_performed": True, "local_owg_append_performed": False,
                "checkpoint_advanced": False, "historical_completeness": "unknown",
                "source_writes_performed": False,
            }
        # The SQLite transaction commits before the local HTTP POST. If the
        # POST becomes uncertain, this durable journal supports manual recovery.
        _, added, replayed = update_journal(
            scope, timezone_name=timezone_name, journal_file=journal,
            grant_file=grant_file, incoming=incoming,
        )
        body, count, current_namespace = _payload(
            scope, timezone_name, journal, grant_file,
        )
        if current_namespace != namespace:
            raise IncrementalError("Source scope changed")
        pre_append = _inventory(transport, port, session)
        events_before = _target(pre_append, log_id, namespace)
        _check_grant(grant_file, scope)
        # A failed/garbled POST response may conceal a committed transaction.
        # Nothing after this point may automatically retry the append.
        attempted = True
        result = append_local(
            log_id, port, body, namespace, session, count, opener=transport,
        )
        _check_grant(grant_file, scope)
        after = _inventory(transport, port, session)
        events_after = _target(after, log_id, namespace)
        if (result["rejected_events"] != 0
                or events_after != events_before + result["added_events"]):
            raise IncrementalError("Local append readback did not verify")
        _check_grant(grant_file, scope)
        return {
            "format": FORMAT, "status": "verified",
            "log_id": log_id, "provider_read_performed": True,
            "new_journal_snapshots": added,
            "replayed_journal_snapshots": replayed,
            "journal_snapshots": count,
            "local_owg_append_performed": True,
            "local_owg_readback_verified": True,
            **result,
            "checkpoint_advanced": False, "historical_completeness": "unknown",
            "source_writes_performed": False,
        }
    except (IncrementalError, BootstrapError, AppendError, JournalError,
            SyncError, StagingError, OSError, ValueError, TypeError, KeyError):
        if attempted:
            raise UncertainIncremental(
                "Inspect local OWG append history before retrying"
            ) from None
        raise IncrementalError(
            "Selected sync refused before OWG append; journal may contain new snapshots"
        ) from None
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None, *, provider_opener=None, opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", action="append", dest="ids", required=True)
    parser.add_argument("--grant-file", required=True, type=Path)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--since")
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--consent-local-journal-update", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = sync_selected(
            instance=args.instance, tenant=args.tenant, incident_ids=args.ids,
            grant_file=args.grant_file, timezone_name=args.timezone,
            journal=args.journal, log_id=args.log_id,
            confirm_log_id=args.confirm_log_id, port=args.port,
            consent_source_read=args.consent_selected_read,
            consent_journal_update=args.consent_local_journal_update,
            consent_local_append=args.consent_local_append,
            since=args.since, token=_connector_token("OWG_SERVICENOW_TOKEN"),
            session=os.environ.get("OWG_DASHBOARD_SESSION"),
            provider_opener=provider_opener, opener=opener,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except UncertainIncremental:
        print(json.dumps({
            "format": FORMAT, "status": "unverified",
            "possible_local_append": True,
            "reason": "Inspect local OWG append history before retrying",
        }, sort_keys=True))
        return 3
    except (IncrementalError, BootstrapError, AppendError, JournalError,
            SyncError, StagingError, OSError, ValueError, TypeError):
        print(json.dumps({
            "format": FORMAT, "status": "refused",
            "possible_local_append": False,
            "reason": "Check scope, grant, session, journal, target and three consents",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
