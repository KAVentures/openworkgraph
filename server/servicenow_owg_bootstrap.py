"""Explicit selected ServiceNow read/journal -> local OWG first import.

By default this imports an existing owner-private journal without contacting
ServiceNow. A separately consented --read-selected mode performs one bounded
provider GET and journals it before the same verified OWG REST bootstrap.
No scheduler, provider write or automatic retry of an uncertain local import.
"""
from __future__ import annotations

from .connector_oauth import connector_token as _connector_token
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request

from .servicenow_incident_journal import (
    JournalError, _csv_rows, _private_directory, _scope_digest,
    update_journal, verify_existing_scope,
)
from .servicenow_incident_staging import SelectedIncidents, StagingError
from .servicenow_incident_sync import (
    SyncError, _check_grant, project_csv, read_selected_once,
)
from .servicenow_owg_append import (
    AppendError, MAX_RESPONSE_BYTES, _LOG, _opener, _pairs_unique,
    _payload, _session,
)

FORMAT = "openworkgraph.servicenow-local-bootstrap.v1"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}\Z", re.ASCII)


class BootstrapError(ValueError):
    """A counts-only, non-sensitive refusal."""


class UncertainBootstrap(BootstrapError):
    """An import may have succeeded; an operator must inspect the log list."""


def _read_json(transport, req):
    try:
        with transport.open(req, timeout=15) as response:
            status, final = response.status, response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, error.URLError, ValueError):
        raise BootstrapError("Local OWG response unavailable") from None
    if (status != 200 or final != req.full_url or media != "application/json"
            or len(raw) > MAX_RESPONSE_BYTES):
        raise BootstrapError("Local OWG response refused")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(BootstrapError()))
    except (ValueError, UnicodeError, TypeError, RecursionError, AppendError):
        raise BootstrapError("Invalid local OWG JSON") from None
    if type(value) is not dict:
        raise BootstrapError("Invalid local OWG response")
    return value


def _inventory(transport, port, session):
    url = f"http://127.0.0.1:{port}/v1/event-logs"
    req = request.Request(url, method="GET", headers={
        "Authorization": "OWG-Session " + session,
        "Accept": "application/json", "Cache-Control": "no-store",
    })
    data = _read_json(transport, req)
    logs = data.get("logs")
    if type(logs) is not list or any(type(row) is not dict for row in logs):
        raise BootstrapError("Unverifiable local log inventory")
    ids = [row.get("log_id") for row in logs]
    if (any(type(item) is not str or not _LOG.fullmatch(item) for item in ids)
            or len(ids) != len(set(ids))):
        raise BootstrapError("Ambiguous local log inventory")
    return logs


def _same_scope(row, namespace):
    identity = row.get("source_identity")
    if identity is None:
        return False
    namespaces = identity.get("namespaces") if type(identity) is dict else None
    if type(namespaces) is not list:
        raise BootstrapError("Unverifiable local source identity")
    if any(type(ns) is not dict for ns in namespaces):
        raise BootstrapError("Invalid local source identity")
    return any(ns.get("source_namespace") == namespace or
               ns.get("case_namespace") == namespace for ns in namespaces)


def _verified(logs, new_id, namespace, count, old_ids):
    if new_id in old_ids:
        raise BootstrapError("Import returned a preexisting log")
    matching = [row for row in logs if row["log_id"] == new_id]
    if len(matching) != 1:
        raise BootstrapError("Imported log not independently readable")
    if any(row["log_id"] != new_id and _same_scope(row, namespace) for row in logs):
        raise BootstrapError("Concurrent duplicate source scope requires review")
    row = matching[0]
    identity = row.get("source_identity")
    namespaces = identity.get("namespaces") if type(identity) is dict else None
    if (type(namespaces) is not list or len(namespaces) != 1
            or type(namespaces[0]) is not dict
            or namespaces[0].get("source_namespace") != namespace
            or namespaces[0].get("case_namespace") != namespace
            or row.get("source_format") != "csv"
            or type(row.get("events")) is not int or row["events"] != count
            or type(row.get("rejected_events")) is not int
            or row["rejected_events"] != 0):
        raise BootstrapError("Imported log counts or namespace did not verify")


def _import(transport, port, session, name, namespace, body, count):
    query = parse.urlencode({
        "format": "csv", "name": name, "case_column": "case_id",
        "activity_column": "activity", "timestamp_column": "timestamp",
        "event_id_column": "source_event_id", "source_namespace": namespace,
        "case_namespace": namespace,
    })
    url = f"http://127.0.0.1:{port}/v1/event-logs/import?{query}"
    req = request.Request(url, data=body, method="POST", headers={
        "Authorization": "OWG-Session " + session,
        "X-OWG-Local-Storage-Confirmed": "true",
        "Content-Type": "text/csv; charset=utf-8",
        "Accept": "application/json", "Cache-Control": "no-store",
    })
    result = _read_json(transport, req)
    new_id = result.get("log_id")
    if (type(new_id) is not str or not _LOG.fullmatch(new_id)
            or type(result.get("events")) is not int or result["events"] != count
            or type(result.get("rejected_events")) is not int
            or result["rejected_events"] != 0):
        raise BootstrapError("Import receipt did not verify all journal events")
    return new_id


def bootstrap(*, instance: str, tenant: str, incident_ids: list[str],
              grant_file: Path, timezone_name: str, journal: Path,
              log_name: str, confirm_log_name: str, port: int,
              consent_journal_read: bool = False, consent_local_import: bool = False,
              session: str | None = None, opener=None,
              read_selected: bool = False, consent_source_read: bool = False,
              since: str | None = None, token: str | None = None,
              provider_opener=None):
    """Create and independently verify one selected-source OWG event log."""
    if consent_journal_read is not True or consent_local_import is not True:
        raise BootstrapError("Separate journal-read and local-import consents required")
    if type(read_selected) is not bool or type(consent_source_read) is not bool:
        raise BootstrapError("Invalid selected-source read mode")
    if read_selected is not consent_source_read or (not read_selected and since is not None):
        raise BootstrapError("Selected-source read requires separate explicit consent")
    if (type(log_name) is not str or not NAME.fullmatch(log_name)
            or confirm_log_name != log_name or type(port) is not int
            or not 1 <= port <= 65535):
        raise BootstrapError("Exact new log name and local port confirmation required")
    session = _session(session)
    scope = SelectedIncidents(host=instance, tenant_scope=tenant,
                              sys_ids=tuple(incident_ids),
                              consented=True, grant_current=True)
    scope.require_authority()
    # The first local log may be bootstrapped from a previously reviewed journal
    # or from one newly authorized selected-source read. Never fetch a provider
    # row without a current grant and a duplicate-source preflight.
    _private_directory(journal.parent)
    lock = journal.with_name(journal.name + ".bootstrap.lock")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock, flags, 0o600)
        os.close(fd)
    except OSError:
        raise BootstrapError("Concurrent or unsafe bootstrap requires review") from None
    try:
        transport = _opener() if opener is None else opener
        _check_grant(grant_file, scope)
        added = replayed = 0
        if read_selected:
            # Preflight the existing journal *and* the destination before any
            # provider HTTPS request. The grant is rechecked by the reader,
            # projection-to-journal transaction and import path.
            verify_existing_scope(scope, timezone_name=timezone_name,
                                  journal_file=journal)
            selected_namespace = "snow_" + _scope_digest(scope, timezone_name)[:32]
            existing = _inventory(transport, port, session)
            if any(_same_scope(row, selected_namespace) for row in existing):
                raise BootstrapError("Selected source already has a local log; use journal append")
            staged = read_selected_once(
                scope, grant_file=grant_file, token=token, since=since,
                opener=provider_opener,
            )
            csv_text, _ = project_csv(scope, staged, timezone_name=timezone_name)
            incoming = _csv_rows(csv_text)
            if not incoming:
                raise BootstrapError("Selected read returned no importable snapshots")
            _, added, replayed = update_journal(
                scope, timezone_name=timezone_name, journal_file=journal,
                grant_file=grant_file, incoming=incoming,
            )
        body, count, namespace = _payload(scope, timezone_name, journal, grant_file)
        # Check again after the selected read: another authorized operator
        # could have created a log while the provider request was in flight.
        before = _inventory(transport, port, session)
        if any(_same_scope(row, namespace) for row in before):
            raise BootstrapError("Selected source already has a local log; use journal append")
        old_ids = {row["log_id"] for row in before}
        _check_grant(grant_file, scope)
        # From this point a failed response may conceal a committed local write.
        try:
            new_id = _import(transport, port, session, log_name, namespace, body, count)
            _check_grant(grant_file, scope)
            after = _inventory(transport, port, session)
            _verified(after, new_id, namespace, count, old_ids)
            _check_grant(grant_file, scope)
        except (BootstrapError, AppendError, JournalError, SyncError, OSError,
                ValueError, TypeError):
            raise UncertainBootstrap("Inspect local log list before retrying") from None
        return {"format": FORMAT, "status": "bootstrapped", "log_id": new_id,
                "journal_snapshots": count, "events": count, "rejected_events": 0,
                "owg_import_readback_verified": True,
                "provider_read_performed": read_selected,
                "new_journal_snapshots": added,
                "replayed_journal_snapshots": replayed,
                "source_writes_performed": False,
                "historical_completeness": "unknown"}
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None, *, opener=None, provider_opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", action="append", dest="ids", required=True)
    parser.add_argument("--grant-file", required=True, type=Path)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--log-name", required=True)
    parser.add_argument("--confirm-log-name", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-local-journal-read", action="store_true")
    parser.add_argument("--consent-local-import", action="store_true")
    parser.add_argument("--read-selected", action="store_true",
                        help="One explicit selected ServiceNow HTTPS GET before first import")
    parser.add_argument("--consent-selected-read", action="store_true",
                        help="Separate consent for the provider GET")
    parser.add_argument("--since", help="Optional explicit provider-local lower bound")
    args = parser.parse_args(argv)
    try:
        result = bootstrap(
            instance=args.instance, tenant=args.tenant, incident_ids=args.ids,
            grant_file=args.grant_file, timezone_name=args.timezone,
            journal=args.journal, log_name=args.log_name,
            confirm_log_name=args.confirm_log_name, port=args.port,
            consent_journal_read=args.consent_local_journal_read,
            consent_local_import=args.consent_local_import,
            read_selected=args.read_selected,
            consent_source_read=args.consent_selected_read,
            since=args.since, token=_connector_token("OWG_SERVICENOW_TOKEN"),
            provider_opener=provider_opener,
            session=os.environ.get("OWG_DASHBOARD_SESSION"), opener=opener)
        print(json.dumps(result, sort_keys=True))
        return 0
    except UncertainBootstrap:
        print(json.dumps({"format": FORMAT, "status": "unverified",
                          "possible_local_import": True,
                          "reason": "Inspect local OWG logs before any retry"}, sort_keys=True))
        return 3
    except (BootstrapError, AppendError, JournalError, StagingError, SyncError,
            OSError, ValueError, TypeError, OverflowError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "possible_local_import": False,
                          "reason": "Check grant, journal, source scope, session, name and consents"},
                         sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
