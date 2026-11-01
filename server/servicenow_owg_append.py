"""Explicit, local-only OWG event-log append from a private ServiceNow journal.

No ServiceNow call, scheduler, hidden upload or provider write. The existing
dashboard-session REST route performs the authorized transactional append.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request

from .servicenow_incident_journal import (
    JournalError, MAX_EVENTS, _csv_rows, _export_csv, _private_directory,
    _scope_digest, update_journal, verify_existing_scope,
)
from .servicenow_incident_staging import SelectedIncidents, StagingError
from .servicenow_incident_sync import SyncError, _check_grant, _NoRedirect

_LOG = re.compile(r"log_[0-9a-f]{16}\Z", re.ASCII)
_SESSION = re.compile(r"[A-Za-z0-9._~+/-]+={0,3}\Z", re.ASCII)
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


class AppendError(ValueError):
    """Non-secret local append refusal."""


def _pairs_unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise AppendError("Ambiguous local OWG response")
        obj[key] = value
    return obj


def _session(value: str | None) -> str:
    if type(value) is not str or not 16 <= len(value) <= 4096 or not _SESSION.fullmatch(value):
        raise AppendError("Local dashboard session missing or invalid")
    return value


def _payload(scope: SelectedIncidents, timezone_name: str, journal: Path,
             grant: Path) -> tuple[bytes, int, str]:
    """Verify the journal and all rows before permitting any local HTTP write."""
    _private_directory(journal.parent)
    _check_grant(grant, scope)
    verify_existing_scope(scope, timezone_name=timezone_name, journal_file=journal)
    rows, added, replayed = update_journal(
        scope, timezone_name=timezone_name, journal_file=journal,
        grant_file=grant, incoming=None,
    )
    if added or replayed or not 1 <= len(rows) <= MAX_EVENTS:
        raise AppendError("Journal is empty or invalid")
    # Reuse the same strict bounded CSV validator as the selected source reader.
    # It accepts at most eight rows at a time, so validate every journal chunk.
    seen = set()
    for start in range(0, len(rows), 8):
        chunk = rows[start:start + 8]
        if len(_csv_rows(_export_csv(chunk))) != len(chunk):
            raise AppendError("Journal row validation failed")
        for row in chunk:
            if row[3] in seen:
                raise AppendError("Duplicate journal source identity")
            seen.add(row[3])
            try:
                stamp = datetime.fromisoformat(row[2])
            except ValueError:
                raise AppendError("Invalid journal timestamp") from None
            if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
                raise AppendError("Journal timestamp must be UTC")
    body = _export_csv(rows).encode("utf-8")
    if len(body) > MAX_UPLOAD_BYTES:
        raise AppendError("Journal export exceeds local append bound")
    namespace = "snow_" + _scope_digest(scope, timezone_name)[:32]
    _check_grant(grant, scope)
    return body, len(rows), namespace


def _opener():
    # The only permitted destination is literal IPv4 loopback, so no DNS,
    # proxy, TLS trust decisions, redirects or provider-supplied next URLs.
    return request.build_opener(
        request.ProxyHandler({}), request.HTTPHandler(), _NoRedirect(),
    )


def append_local(log_id: str, port: int, body: bytes, namespace: str,
                 session: str, *, opener=None) -> dict:
    if not _LOG.fullmatch(log_id) or type(port) is not int or not 1 <= port <= 65535:
        raise AppendError("Invalid local event-log target")
    if not re.fullmatch(r"snow_[0-9a-f]{32}", namespace, re.ASCII):
        raise AppendError("Invalid selected-source namespace")
    if type(body) is not bytes or not 1 <= len(body) <= MAX_UPLOAD_BYTES:
        raise AppendError("Invalid local append payload")
    session = _session(session)
    query = parse.urlencode({
        "format": "csv", "case_column": "case_id",
        "activity_column": "activity", "timestamp_column": "timestamp",
        "event_id_column": "source_event_id",
        "source_namespace": namespace, "case_namespace": namespace,
    })
    url = f"http://127.0.0.1:{port}/v1/event-logs/{log_id}/append?{query}"
    req = request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": "OWG-Session " + session,
            "x-owg-local-storage-confirmed": "true",
            "Content-Type": "text/csv; charset=utf-8",
            "Accept": "application/json",
        },
    )
    transport = _opener() if opener is None else opener
    try:
        with transport.open(req, timeout=15) as response:
            status = response.status
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, error.URLError, ValueError):
        raise AppendError("Local OWG append result unverified") from None
    if (status != 200 or final_url != url
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
            or len(raw) > MAX_RESPONSE_BYTES):
        raise AppendError("Local OWG append result unverified")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_unique)
    except (ValueError, UnicodeError):
        raise AppendError("Local OWG append result unverified") from None
    if (type(result) is not dict or type(result.get("log")) is not dict
            or result["log"].get("log_id") != log_id
            or any(type(result.get(key)) is not int or result[key] < 0
                   for key in ("batch", "added_events", "duplicate_events_skipped",
                               "rejected_events"))
            or result["batch"] < 1):
        raise AppendError("Local OWG append result unverified")
    return {key: result[key] for key in (
        "batch", "added_events", "duplicate_events_skipped", "rejected_events",
    )}


def main(argv: list[str] | None = None, *, opener=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", action="append", dest="ids", required=True)
    parser.add_argument("--grant-file", type=Path, required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-local-journal-read", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    args = parser.parse_args(argv)
    attempted = False
    try:
        if not args.consent_local_journal_read or not args.consent_local_append:
            raise AppendError("Explicit journal-read and local-append consent required")
        if not _LOG.fullmatch(args.log_id) or args.confirm_log_id != args.log_id:
            raise AppendError("Explicit exact target log confirmation required")
        if not 1 <= args.port <= 65535:
            raise AppendError("Invalid loopback port")
        session = _session(os.environ.get("OWG_DASHBOARD_SESSION"))
        scope = SelectedIncidents(
            host=args.instance, tenant_scope=args.tenant, sys_ids=tuple(args.ids),
            consented=True, grant_current=True,
        )
        scope.require_authority()
        body, count, namespace = _payload(
            scope, args.timezone, args.journal, args.grant_file,
        )
        _check_grant(args.grant_file, scope)
        attempted = True
        result = append_local(
            args.log_id, args.port, body, namespace, session, opener=opener,
        )
        _check_grant(args.grant_file, scope)
        print(json.dumps({
            "status": "completed" if result["rejected_events"] == 0
                      else "completed_with_rejections",
            "format": "openworkgraph.servicenow-local-append.v1",
            "journal_snapshots": count,
            **result,
            "source_writes_performed": False,
            "provider_read_performed": False,
            "local_owg_append_performed": True,
            "historical_completeness": "unknown",
        }, sort_keys=True))
        return 0 if result["rejected_events"] == 0 else 3
    except (AppendError, JournalError, SyncError, StagingError, OSError,
            ValueError, OverflowError, TypeError, KeyError):
        # A transport failure, malformed response or revocation after the POST
        # cannot prove the local transaction did not commit. Never auto-retry.
        print(json.dumps({
            "status": "unverified" if attempted else "refused",
            "possible_local_append": attempted,
            "reason": "Inspect the local OWG append history before retrying"
                      if attempted else
                      "Check private journal, matching grant, consent, session and target",
        }, sort_keys=True))
        return 3 if attempted else 2


if __name__ == "__main__":
    sys.exit(main())
