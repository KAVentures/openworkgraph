"""Explicit ServiceNow sys_audit field-change read -> private CSV -> local OWG append.

This is a bounded *observation* of selected incident audit rows, not a complete
history, a business-outcome claim, or a background source synchronization.
No old/new field values are requested, retained or imported.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib import error, parse, request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .servicenow_incident_journal import JournalError, _private_directory, _scope_digest
from .servicenow_incident_staging import SelectedIncidents, StagingError, _validate_updated
from .servicenow_incident_sync import (
    SyncError, _bearer, _check_grant, _http_opener, _publish_new,
    _source_local_to_utc, _unique_pairs,
)
from .servicenow_owg_append import (
    AppendError, MAX_UPLOAD_BYTES, _LOG, _session, append_local,
    verify_local_target,
)

FORMAT = "openworkgraph.servicenow-audit-observations.v1"
ALLOWED_FIELDS = frozenset({
    "state", "priority", "assignment_group", "assigned_to",
    "category", "impact", "urgency",
})
PAGE_SIZE = 100
MAX_PAGES = 4
MAX_RESPONSE_BYTES = 262_144
MAX_WINDOW = timedelta(days=7)
_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_GRANT_KEYS = frozenset({
    "version", "enabled", "consented", "host", "tenant_scope", "sys_ids",
    "table", "fields", "since", "until", "timezone",
})
_ROW_KEYS = frozenset({
    "sys_id", "documentkey", "fieldname", "sys_created_on", "tablename",
})
_CSV_FIELDS = ("case_id", "activity", "timestamp", "source_event_id")


class AuditError(ValueError):
    """Minimized error; never includes incident IDs, provider values or secrets."""


def _window(since: str, until: str, zone_name: str) -> None:
    if type(zone_name) is not str or not 1 <= len(zone_name) <= 100:
        raise AuditError("Invalid declared source timezone")
    try:
        zone = ZoneInfo(zone_name)
        _validate_updated(since)
        _validate_updated(until)
        first = datetime.fromisoformat(_source_local_to_utc(since, zone))
        last = datetime.fromisoformat(_source_local_to_utc(until, zone))
    except (ValueError, KeyError, ZoneInfoNotFoundError):
        raise AuditError("Invalid or ambiguous source-local audit window") from None
    if not first < last or last - first > MAX_WINDOW:
        raise AuditError("Audit window must be positive and at most seven days")


def _check_audit_grant(path: Path, scope: SelectedIncidents, *,
                       fields: tuple[str, ...], since: str, until: str,
                       zone_name: str) -> None:
    """Independent owner-only grant for sys_audit, not inherited incident access."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size > 4096 or
                    (os.name == "posix" and
                     (info.st_uid != os.getuid() or info.st_mode & 0o077))):
                raise AuditError("Invalid private audit grant")
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise AuditError("Invalid private audit grant")
        grant = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise AuditError("Audit grant missing, revoked or invalid") from None
    if (type(grant) is not dict or set(grant) != _GRANT_KEYS
            or type(grant["version"]) is not int or grant["version"] != 1
            or grant["enabled"] is not True or grant["consented"] is not True
            or grant["table"] != "sys_audit"
            or grant["host"] != scope.host or grant["tenant_scope"] != scope.tenant_scope
            or type(grant["sys_ids"]) is not list
            or any(type(x) is not str for x in grant["sys_ids"])
            or sorted(grant["sys_ids"]) != sorted(scope.sys_ids)
            or type(grant["fields"]) is not list
            or any(type(x) is not str for x in grant["fields"])
            or sorted(grant["fields"]) != sorted(fields)
            or grant["since"] != since or grant["until"] != until
            or grant["timezone"] != zone_name):
        raise AuditError("Audit grant revoked or does not match exact scope")


def _url(scope: SelectedIncidents, *, fields: tuple[str, ...], since: str, until: str, offset: int) -> str:
    if type(offset) is not int or offset not in range(0, PAGE_SIZE * MAX_PAGES, PAGE_SIZE):
        raise AuditError("Invalid bounded audit page offset")
    query = ("tablename=incident^documentkeyIN" + ",".join(sorted(scope.sys_ids))
             + "^fieldnameIN" + ",".join(sorted(fields))
             + "^sys_created_on>=" + since + "^sys_created_on<=" + until
             + "^ORDERBYsys_created_on^ORDERBYsys_id")
    params = {
        "sysparm_query": query,
        "sysparm_fields": "sys_id,documentkey,fieldname,sys_created_on,tablename",
        "sysparm_display_value": "false",
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": str(PAGE_SIZE),
        "sysparm_offset": str(offset),
    }
    return "https://" + scope.host + "/api/now/table/sys_audit?" + parse.urlencode(params)


def _parse_page(raw: bytes, *, scope: SelectedIncidents,
                fields: tuple[str, ...], since: str, until: str,
                zone_name: str) -> list[tuple[str, str, str, str]]:
    if type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
        raise AuditError("Audit page exceeds byte bound")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError, TypeError):
        raise AuditError("Invalid source audit JSON") from None
    if type(payload) is not dict or set(payload) != {"result"}:
        raise AuditError("Invalid audit response envelope")
    rows = payload["result"]
    if type(rows) is not list or len(rows) > PAGE_SIZE:
        raise AuditError("Invalid bounded audit page")
    zone = ZoneInfo(zone_name)
    result = []
    for row in rows:
        if (type(row) is not dict or set(row) != _ROW_KEYS
                or any(type(row[key]) is not str for key in _ROW_KEYS)
                or not _ID.fullmatch(row["sys_id"])
                or row["documentkey"] not in scope.sys_ids
                or row["tablename"] != "incident"
                or row["fieldname"] not in fields):
            raise AuditError("Unexpected audit row or out-of-scope record")
        stamp = row["sys_created_on"]
        _validate_updated(stamp)
        if not since <= stamp <= until:
            raise AuditError("Audit observation outside authorized window")
        case_id = "snowcase:" + hashlib.sha256(
            (scope.namespace + "\0" + row["documentkey"]).encode("ascii")
        ).hexdigest()
        event_id = "snowevent:" + hashlib.sha256(
            (scope.namespace + "\0audit\0" + row["sys_id"]).encode("ascii")
        ).hexdigest()
        result.append((case_id, "Incident audit: " + row["fieldname"],
                       _source_local_to_utc(stamp, zone), event_id))
    return result


def _read_audit(scope: SelectedIncidents, *, grant_file: Path,
                audit_grant_file: Path, fields: tuple[str, ...],
                since: str, until: str, zone_name: str,
                token: str, opener=None) -> list[tuple[str, str, str, str]]:
    bearer = _bearer(token)
    transport = _http_opener() if opener is None else opener
    events = {}
    for page in range(MAX_PAGES):
        _check_grant(grant_file, scope)
        _check_audit_grant(audit_grant_file, scope, fields=fields,
                           since=since, until=until, zone_name=zone_name)
        url = _url(scope, fields=fields, since=since, until=until, offset=page * PAGE_SIZE)
        req = request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + bearer,
            "Accept": "application/json",
        })
        try:
            with transport.open(req, timeout=15) as response:
                status = response.status
                final_url = response.geturl()
                media = response.headers.get("Content-Type", "")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, error.URLError, ValueError):
            raise AuditError("ServiceNow audit HTTPS read failed") from None
        if (status != 200 or final_url != url
                or media.split(";", 1)[0].strip().lower() != "application/json"):
            raise AuditError("ServiceNow audit HTTP response not accepted")
        rows = _parse_page(raw, scope=scope, fields=fields, since=since,
                           until=until, zone_name=zone_name)
        _check_grant(grant_file, scope)
        _check_audit_grant(audit_grant_file, scope, fields=fields,
                           since=since, until=until, zone_name=zone_name)
        for row in rows:
            if row[3] in events:
                # Offset pagination can shift during concurrent source writes.
                # Never call a duplicate page a complete historical extraction.
                raise AuditError("Duplicate audit row across bounded pages")
            events[row[3]] = row
        if len(rows) < PAGE_SIZE:
            return sorted(events.values(), key=lambda r: (r[0], r[2], r[3]))
    raise AuditError("Audit pagination reached limit; completeness unverified")


def _csv_bytes(rows: list[tuple[str, str, str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(_CSV_FIELDS)
    writer.writerows(rows)
    body = output.getvalue().encode("utf-8")
    if len(body) > MAX_UPLOAD_BYTES:
        raise AuditError("Audit recovery CSV exceeds local append bound")
    return body


def main(argv: list[str] | None = None, *, provider_opener=None,
         local_opener=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--incident-id", dest="ids", action="append", required=True)
    parser.add_argument("--field", dest="fields", action="append", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--grant-file", type=Path, required=True)
    parser.add_argument("--audit-grant-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-audit-read", action="store_true")
    parser.add_argument("--consent-recovery-export", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    args = parser.parse_args(argv)

    output_created = False
    possible_append = False
    try:
        if not all((args.consent_audit_read, args.consent_recovery_export,
                    args.consent_local_append)):
            raise AuditError("Three explicit audit/read/export/append consents required")
        fields = tuple(args.fields)
        if (not 1 <= len(fields) <= len(ALLOWED_FIELDS)
                or len(set(fields)) != len(fields)
                or any(type(x) is not str or x not in ALLOWED_FIELDS for x in fields)):
            raise AuditError("Unsupported or duplicate audit field selection")
        if not _LOG.fullmatch(args.log_id) or args.confirm_log_id != args.log_id:
            raise AuditError("Exact local log confirmation required")
        if type(args.port) is not int or not 1 <= args.port <= 65535:
            raise AuditError("Invalid local port")
        _window(args.since, args.until, args.timezone)
        scope = SelectedIncidents(
            host=args.instance, tenant_scope=args.tenant,
            sys_ids=tuple(args.ids), consented=True, grant_current=True,
        )
        scope.require_authority()
        paths = [os.path.abspath(x) for x in (
            args.grant_file, args.audit_grant_file, args.output,
        )]
        if len(set(paths)) != len(paths):
            raise AuditError("Private grant and recovery paths must be distinct")
        _private_directory(args.output.parent)
        if args.output.exists() or args.output.is_symlink():
            raise AuditError("Recovery output already exists")
        _check_grant(args.grant_file, scope)
        _check_audit_grant(args.audit_grant_file, scope, fields=fields,
                           since=args.since, until=args.until, zone_name=args.timezone)
        session = _session(os.environ.get("OWG_DASHBOARD_SESSION"))
        namespace = "snow_" + _scope_digest(scope, args.timezone)[:32]
        # Refuse a wrong target before the first provider GET.
        verify_local_target(args.log_id, args.port, namespace, session,
                            opener=local_opener)
        _check_grant(args.grant_file, scope)
        _check_audit_grant(args.audit_grant_file, scope, fields=fields,
                           since=args.since, until=args.until, zone_name=args.timezone)
        rows = _read_audit(
            scope, grant_file=args.grant_file, audit_grant_file=args.audit_grant_file,
            fields=fields, since=args.since, until=args.until,
            zone_name=args.timezone, token=os.environ.get("OWG_SERVICENOW_TOKEN"),
            opener=provider_opener,
        )
        body = _csv_bytes(rows)
        _check_audit_grant(args.audit_grant_file, scope, fields=fields,
                           since=args.since, until=args.until, zone_name=args.timezone)
        _publish_new(args.output, body.decode("utf-8"),
                     grant_file=args.grant_file, scope=scope)
        output_created = True
        _check_audit_grant(args.audit_grant_file, scope, fields=fields,
                           since=args.since, until=args.until, zone_name=args.timezone)
        _check_grant(args.grant_file, scope)
        verify_local_target(args.log_id, args.port, namespace, session,
                            opener=local_opener)
        _check_audit_grant(args.audit_grant_file, scope, fields=fields,
                           since=args.since, until=args.until, zone_name=args.timezone)
        _check_grant(args.grant_file, scope)
        result = None
        if rows:
            possible_append = True
            result = append_local(
                args.log_id, args.port, body, namespace, session, len(rows),
                opener=local_opener,
            )
            _check_audit_grant(args.audit_grant_file, scope, fields=fields,
                               since=args.since, until=args.until, zone_name=args.timezone)
            _check_grant(args.grant_file, scope)
        if result is not None and result["rejected_events"]:
            raise AuditError("Local append reported rejected events")
        print(json.dumps({
            "format": FORMAT, "status": "completed",
            "audit_observations": len(rows),
            "added_events": result["added_events"] if result else 0,
            "duplicate_events_skipped": result["duplicate_events_skipped"] if result else 0,
            "recovery_csv_created": True,
            "local_owg_append_performed": bool(rows),
            "source_writes_performed": False,
            "historical_completeness": "unknown",
            "old_new_values_requested": False,
            "automatic_checkpoint": False,
        }, sort_keys=True))
        return 0
    except (AuditError, SyncError, StagingError, JournalError, AppendError,
            OSError, ValueError, OverflowError, TypeError, KeyError):
        print(json.dumps({
            "format": FORMAT,
            "status": "append_unverified" if possible_append else "refused",
            "possible_local_append": possible_append,
            "recovery_csv_may_exist": output_created,
            "source_writes_performed": False,
            "reason": ("Inspect the local log and recovery CSV before any retry"
                       if possible_append else
                       "Check separate grants, exact scope, consent, local target and source"),
        }, sort_keys=True))
        return 3 if possible_append else 2


if __name__ == "__main__":
    sys.exit(main())
