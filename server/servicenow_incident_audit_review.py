"""Explicit two-phase ServiceNow audit import: bounded HTTPS prepare, reviewed local append.

Neither phase writes to ServiceNow. Prepared CSV and manifest remain private.
"""
from __future__ import annotations

from .connector_oauth import connector_token as _connector_token
import argparse
import csv
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
from zoneinfo import ZoneInfo

from .servicenow_incident_audit import (
    ALLOWED_FIELDS, AuditError, _check_audit_grant, _csv_bytes, _read_audit, _window,
)
from .servicenow_incident_journal import JournalError, _private_directory, _scope_digest
from .servicenow_incident_staging import SelectedIncidents, StagingError
from .servicenow_incident_sync import (
    SyncError, _check_grant, _publish_new, _source_local_to_utc,
)
from .servicenow_owg_append import (
    AppendError, MAX_UPLOAD_BYTES, _LOG, _session, append_local, verify_local_target,
)

FORMAT = "openworkgraph.servicenow-audit-reviewed-append.v1"
KEYS = frozenset({"format", "scope_digest", "namespace", "log_id", "fields",
                  "since", "until", "timezone", "csv_sha256", "rows"})
CSV_FIELDS = ("case_id", "activity", "timestamp", "source_event_id")
HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class AuditReviewError(ValueError):
    """Minimized local refusal."""


class UncertainAppend(AuditReviewError):
    """A local append may have happened; do not automatically retry."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditReviewError("Duplicate manifest attribute")
        result[key] = value
    return result


def _read_private(path: Path, limit: int) -> bytes:
    _private_directory(path.parent)
    if path.is_symlink():
        raise AuditReviewError("Prepared artifact symlink refused")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size > limit or
                    (os.name == "posix" and
                     (info.st_uid != os.getuid() or info.st_mode & 0o077))):
                raise AuditReviewError("Unsafe prepared artifact")
            body = stream.read(limit + 1)
    except OSError:
        raise AuditReviewError("Prepared artifact unavailable") from None
    if not body or len(body) > limit:
        raise AuditReviewError("Prepared artifact empty or oversized")
    return body


def _scope(args):
    fields = tuple(args.fields)
    if (not 1 <= len(fields) <= len(ALLOWED_FIELDS)
            or len(set(fields)) != len(fields)
            or any(field not in ALLOWED_FIELDS for field in fields)):
        raise AuditReviewError("Invalid audit field selection")
    _window(args.since, args.until, args.timezone)
    scope = SelectedIncidents(
        host=args.instance, tenant_scope=args.tenant, sys_ids=tuple(args.ids),
        consented=True, grant_current=True,
    )
    scope.require_authority()
    if not _LOG.fullmatch(args.log_id) or args.confirm_log_id != args.log_id:
        raise AuditReviewError("Exact destination log confirmation required")
    if type(args.port) is not int or not 1 <= args.port <= 65535:
        raise AuditReviewError("Invalid local port")
    paths = [args.grant_file, args.audit_grant_file, args.csv, args.manifest]
    for path in paths:
        _private_directory(path.parent)
    if len({os.path.abspath(path) for path in paths}) != 4:
        raise AuditReviewError("Grant and prepared paths must be distinct")
    return scope, fields


def _authority(args, scope, fields):
    _check_grant(args.grant_file, scope)
    _check_audit_grant(args.audit_grant_file, scope, fields=fields,
                       since=args.since, until=args.until, zone_name=args.timezone)


def _manifest(args, scope, fields, body: bytes, count: int):
    digest = _scope_digest(scope, args.timezone)
    return {
        "format": FORMAT, "scope_digest": digest,
        "namespace": "snow_" + digest[:32], "log_id": args.log_id,
        "fields": sorted(fields), "since": args.since, "until": args.until,
        "timezone": args.timezone, "csv_sha256": hashlib.sha256(body).hexdigest(),
        "rows": count,
    }


def _count_csv(body: bytes, args, fields) -> int:
    try:
        reader = csv.reader(io.StringIO(body.decode("utf-8"), newline=""), strict=True)
        if tuple(next(reader)) != CSV_FIELDS:
            raise AuditReviewError("Prepared CSV schema mismatch")
        zone = ZoneInfo(args.timezone)
        lower = datetime.fromisoformat(_source_local_to_utc(args.since, zone))
        upper = datetime.fromisoformat(_source_local_to_utc(args.until, zone))
        seen = set()
        count = 0
        for row in reader:
            if len(row) != 4 or count >= 400:
                raise AuditReviewError("Prepared CSV row bound or shape mismatch")
            case, activity, stamp, event = row
            if (not case.startswith("snowcase:") or not HASH.fullmatch(case[9:])
                    or not event.startswith("snowevent:") or not HASH.fullmatch(event[10:])
                    or not activity.startswith("Incident audit: ")
                    or activity[16:] not in fields or event in seen):
                raise AuditReviewError("Prepared CSV identity or activity mismatch")
            instant = datetime.fromisoformat(stamp)
            if (instant.tzinfo is None or instant.utcoffset().total_seconds() != 0
                    or not lower <= instant <= upper):
                raise AuditReviewError("Prepared CSV time outside authorized window")
            seen.add(event)
            count += 1
        return count
    except (UnicodeError, csv.Error, StopIteration, ValueError, TypeError, OverflowError):
        raise AuditReviewError("Invalid prepared audit CSV") from None


def prepare(args, *, provider_opener=None, local_opener=None):
    if not (args.consent_audit_read and args.consent_recovery_export):
        raise AuditReviewError("Explicit audit-read and recovery-export consents required")
    scope, fields = _scope(args)
    if any(path.exists() or path.is_symlink() for path in (args.csv, args.manifest)):
        raise AuditReviewError("Prepared output already exists")
    session = _session(os.environ.get("OWG_DASHBOARD_SESSION"))
    namespace = _manifest(args, scope, fields, b"", 0)["namespace"]
    _authority(args, scope, fields)
    verify_local_target(args.log_id, args.port, namespace, session, opener=local_opener)
    _authority(args, scope, fields)
    rows = _read_audit(
        scope, grant_file=args.grant_file, audit_grant_file=args.audit_grant_file,
        fields=fields, since=args.since, until=args.until, zone_name=args.timezone,
        token=_connector_token("OWG_SERVICENOW_TOKEN"), opener=provider_opener,
    )
    body = _csv_bytes(rows)
    _authority(args, scope, fields)
    _publish_new(args.csv, body.decode("utf-8"), grant_file=args.grant_file, scope=scope)
    manifest = _manifest(args, scope, fields, body, len(rows))
    _authority(args, scope, fields)
    _publish_new(args.manifest, json.dumps(manifest, sort_keys=True) + "\n",
                 grant_file=args.grant_file, scope=scope)
    _authority(args, scope, fields)
    return {
        "format": FORMAT, "status": "prepared", "audit_observations": len(rows),
        "csv_sha256": manifest["csv_sha256"], "review_required": True,
        "local_owg_append_performed": False, "source_writes_performed": False,
        "historical_completeness": "unknown",
    }


def apply(args, *, local_opener=None):
    if not all((args.consent_prepared_read, args.consent_local_append,
                args.consent_reviewed_apply)):
        raise AuditReviewError("Three explicit review/append consents required")
    scope, fields = _scope(args)
    session = _session(os.environ.get("OWG_DASHBOARD_SESSION"))
    _authority(args, scope, fields)
    try:
        manifest = json.loads(_read_private(args.manifest, 4096).decode("utf-8"),
                              object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, TypeError):
        raise AuditReviewError("Invalid prepared manifest") from None
    body = _read_private(args.csv, MAX_UPLOAD_BYTES)
    expected = _manifest(args, scope, fields, body, 0)
    if (type(manifest) is not dict or set(manifest) != KEYS
            or any(type(manifest[key]) is not type(expected[key]) for key in KEYS)
            or any(manifest[key] != expected[key] for key in KEYS - {"rows"})
            or not 0 <= manifest["rows"] <= 400
            or type(args.confirm_sha256) is not str
            or not HASH.fullmatch(args.confirm_sha256)
            or args.confirm_sha256 != manifest["csv_sha256"]):
        raise AuditReviewError("Prepared manifest, scope or review digest mismatch")
    count = _count_csv(body, args, fields)
    if count != manifest["rows"]:
        raise AuditReviewError("Prepared CSV count mismatch")
    verify_local_target(args.log_id, args.port, expected["namespace"], session,
                        opener=local_opener)
    _authority(args, scope, fields)
    if not count:
        return {
            "format": FORMAT, "status": "applied", "audit_observations": 0,
            "added_events": 0, "duplicate_events_skipped": 0,
            "local_owg_append_performed": False, "source_writes_performed": False,
            "historical_completeness": "unknown",
        }
    try:
        result = append_local(args.log_id, args.port, body, expected["namespace"],
                              session, count, opener=local_opener)
        if result["rejected_events"]:
            raise UncertainAppend("Local append rejected prepared rows")
        _authority(args, scope, fields)
    except (AppendError, SyncError, AuditError, JournalError, ValueError, OSError):
        raise UncertainAppend("Local append outcome unverified") from None
    return {
        "format": FORMAT, "status": "applied", "audit_observations": count,
        "added_events": result["added_events"],
        "duplicate_events_skipped": result["duplicate_events_skipped"],
        "local_owg_append_performed": True, "source_writes_performed": False,
        "historical_completeness": "unknown",
    }


def main(argv=None, *, provider_opener=None, local_opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("prepare", "apply"):
        cmd = sub.add_parser(name)
        for flag in ("instance", "tenant", "since", "until", "timezone",
                     "log-id", "confirm-log-id"):
            cmd.add_argument("--" + flag, required=True)
        cmd.add_argument("--incident-id", action="append", dest="ids", required=True)
        cmd.add_argument("--field", action="append", dest="fields", required=True)
        for flag in ("grant-file", "audit-grant-file", "csv", "manifest"):
            cmd.add_argument("--" + flag, type=Path, required=True)
        cmd.add_argument("--port", type=int, default=8765)
        if name == "prepare":
            cmd.add_argument("--consent-audit-read", action="store_true")
            cmd.add_argument("--consent-recovery-export", action="store_true")
        else:
            cmd.add_argument("--confirm-sha256", required=True)
            cmd.add_argument("--consent-prepared-read", action="store_true")
            cmd.add_argument("--consent-local-append", action="store_true")
            cmd.add_argument("--consent-reviewed-apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = (prepare(args, provider_opener=provider_opener,
                          local_opener=local_opener)
                  if args.action == "prepare" else
                  apply(args, local_opener=local_opener))
        print(json.dumps(report, sort_keys=True))
        return 0
    except UncertainAppend:
        print(json.dumps({
            "format": FORMAT, "status": "append_unverified",
            "possible_local_append": True, "source_writes_performed": False,
            "reason": "Inspect the local log before any manual retry",
        }, sort_keys=True))
        return 3
    except (AuditReviewError, AuditError, JournalError, StagingError,
            SyncError, AppendError, OSError, ValueError, TypeError,
            KeyError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused", "possible_local_append": False,
            "source_writes_performed": False,
            "reason": "Check grants, private artifacts, scope, review and consent",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
