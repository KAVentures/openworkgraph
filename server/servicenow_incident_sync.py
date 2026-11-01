"""Opt-in, one-shot ServiceNow selected-incident HTTPS read -> OWG CSV export.

No background sync, source writes, stored credentials, automatic checkpoint,
or authorization to append. See docs/SERVICENOW_INCIDENT_SYNC.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import tempfile
from datetime import datetime, timezone
from urllib import error, request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .servicenow_incident_staging import (
    MAX_BYTES, SelectedIncidents, StagingError, plan_page,
    project_supplied_http_response,
)

_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+={0,3}\Z", re.ASCII)
_GRANT_KEYS = frozenset({"version", "enabled", "consented", "host", "tenant_scope", "sys_ids"})
_CSV_FIELDS = ("case_id", "activity", "timestamp", "source_event_id")


class SyncError(ValueError):
    """Generic, non-secret refusal for selected source reads."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise SyncError("Invalid local selected-source grant")
        out[key] = value
    return out


def _check_grant(path: Path, scope: SelectedIncidents) -> None:
    """Re-read operator-managed local consent/grant marker; never trust a cached flag.

    This is a local opt-in gate, not an authenticated enterprise permission
    service or an atomic fence against concurrent external revocation.
    """
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                raise SyncError("Invalid local selected-source grant")
            if os.name == "posix" and info.st_mode & 0o077:
                raise SyncError("Local selected-source grant must be owner-only")
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise SyncError("Invalid local selected-source grant")
        grant = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise SyncError("Selected-source grant missing, revoked or invalid") from None
    if (type(grant) is not dict or set(grant) != _GRANT_KEYS
            or type(grant["version"]) is not int or grant["version"] != 1
            or grant["enabled"] is not True or grant["consented"] is not True
            or type(grant["host"]) is not str or grant["host"] != scope.host
            or type(grant["tenant_scope"]) is not str or grant["tenant_scope"] != scope.tenant_scope
            or type(grant["sys_ids"]) is not list
            or len(grant["sys_ids"]) != len(scope.sys_ids)
            or any(type(item) is not str for item in grant["sys_ids"])
            or sorted(grant["sys_ids"]) != sorted(scope.sys_ids)):
        raise SyncError("Selected-source grant missing, revoked or mismatched")


def _bearer(token: str | None) -> str:
    if type(token) is not str or not 1 <= len(token) <= 4096 or not _TOKEN.fullmatch(token):
        raise SyncError("Missing or invalid ServiceNow bearer credential")
    return token


def _http_opener():
    # No ambient HTTP(S) proxy, no redirects, default system trust roots and
    # certificate/hostname verification. A custom ServiceNow domain is not allowed.
    return request.build_opener(
        request.ProxyHandler({}),
        request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )


def read_selected_once(
    scope: SelectedIncidents, *, grant_file: Path, token: str,
    since: str | None = None, opener=None,
) -> dict:
    """Perform at most one exact selected-record GET; return minimized snapshots.

    A single page of 16 slots covers at most eight explicitly selected IDs.
    Refuse a full page or duplicates instead of silently treating it as a
    complete selected snapshot. Source history and omitted IDs remain unknown.
    """
    if type(scope) is not SelectedIncidents:
        raise SyncError("Invalid selected-source scope")
    scope.require_authority()
    _check_grant(grant_file, scope)
    bearer = _bearer(token)
    plan = plan_page(scope, offset=0, limit=16, since=since)
    req = request.Request(
        plan["url"], headers={"Authorization": "Bearer " + bearer,
                              "Accept": "application/json"}, method="GET",
    )
    transport = opener if opener is not None else _http_opener()
    try:
        with transport.open(req, timeout=15) as response:
            status = response.status
            final_url = response.geturl()
            media_type = response.headers.get("Content-Type", "")
            raw = response.read(MAX_BYTES + 1)
    except (OSError, error.URLError, ValueError) as exc:
        raise SyncError("Selected-source HTTPS GET failed or was refused") from None
    # Even if the provider returns a success-shaped JSON body, never accept
    # redirects, unexpected final URLs, non-200 status or non-UTF-8 JSON.
    staged = project_supplied_http_response(
        scope, plan=plan, request_method=req.get_method(),
        request_url=req.full_url, response_url=final_url, status=status,
        content_type=media_type, redirect_count=0, body=raw,
    )
    if staged["may_have_more"] or staged["returned_rows"] != staged["distinct_selected_rows"]:
        raise SyncError("Selected-source response may be truncated or duplicated")
    _check_grant(grant_file, scope)
    return staged


def _source_local_to_utc(value: str, zone: ZoneInfo) -> str:
    """Reject DST gaps and folds; a declared zone is not provider-verified."""
    local = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    possible = set()
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == local:
            possible.add(utc)
    if len(possible) != 1:
        raise SyncError("Ambiguous or nonexistent source-local timestamp")
    return next(iter(possible)).isoformat()


def project_csv(scope: SelectedIncidents, staged: dict, *, timezone_name: str) -> tuple[str, dict]:
    """Normalize source snapshots for the existing explicit CSV import/append."""
    if type(timezone_name) is not str or not 1 <= len(timezone_name) <= 100:
        raise SyncError("Declare the source instance IANA timezone")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise SyncError("Unknown source instance IANA timezone") from None
    if (type(staged) is not dict or staged.get("not_an_authorization_to_append") is not True
            or staged.get("plan_bound") is not True
            or staged.get("source_namespace") not in (None, scope.namespace)):
        raise SyncError("Invalid staged selected-source response")
    records = staged.get("records")
    if type(records) is not list or len(records) > len(scope.sys_ids):
        raise SyncError("Invalid staged selected-source records")
    events = {}
    for item in records:
        if (type(item) is not dict or item.get("source_namespace") != scope.namespace
                or item.get("source_case_id") not in scope.sys_ids
                or type(item.get("source_event_id")) is not str
                or type(item.get("reported_state")) is not str
                or type(item.get("reported_updated_on")) is not str):
            raise SyncError("Invalid staged selected-source record")
        case_key = "snowcase:" + hashlib.sha256(
            (scope.namespace + "\0" + item["source_case_id"]).encode("ascii")
        ).hexdigest()
        event_key = "snowevent:" + hashlib.sha256(
            (scope.namespace + "\0" + item["source_event_id"]).encode("ascii")
        ).hexdigest()
        event = (case_key, "Incident state: " + item["reported_state"],
                 _source_local_to_utc(item["reported_updated_on"], zone), event_key)
        if event_key in events and events[event_key] != event:
            raise SyncError("Conflicting selected-source revision")
        events[event_key] = event
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(_CSV_FIELDS)
    writer.writerows(sorted(events.values(), key=lambda row: (row[0], row[2], row[3])))
    report = {
        "format": "openworkgraph.servicenow-selected-incident.v1",
        "selected_incidents": len(scope.sys_ids),
        "represented_incidents": len({row[0] for row in events.values()}),
        "source_snapshots": len(events),
        "unobserved_selected_incidents": len(staged["unobserved_selected_ids"]),
        "historical_completeness": "unknown",
        "source_timestamp_timezone": "operator_declared_not_provider_verified",
        "business_outcome_verified": False,
        "source_writes_performed": False,
        "checkpoint_advanced": False,
        "automatic_append_performed": False,
    }
    return output.getvalue(), report


def _publish_new(path: Path, csv_text: str, *, grant_file: Path, scope: SelectedIncidents) -> None:
    """Publish a fully written 0600 CSV under a new name, never overwrite."""
    temp_name = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".owg-snow-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(csv_text)
            output.flush()
            os.fsync(output.fileno())
        _check_grant(grant_file, scope)
        # Same-filesystem hard link atomically refuses existing output paths.
        # No automatic checkpoint or existing OWG log is modified.
        os.link(temp_name, path)
    except (OSError, ValueError) as exc:
        raise SyncError("Could not publish new selected-source CSV") from None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def main(argv: list[str] | None = None, *, opener=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True, help="Exact lowercase *.service-now.com host")
    parser.add_argument("--tenant", required=True, help="Local tenant namespace (not a verified provider identity)")
    parser.add_argument("--incident-id", action="append", required=True, dest="ids")
    parser.add_argument("--grant-file", required=True, type=Path, help="Owner-only, explicitly enabled matching grant JSON")
    parser.add_argument("--consent-selected-read", action="store_true", help="Explicit opt-in for this one-shot source read")
    parser.add_argument("--timezone", required=True, help="Operator-declared source instance IANA timezone")
    parser.add_argument("--since", help="Optional provider-local YYYY-MM-DD HH:MM:SS lower bound")
    parser.add_argument("--output", required=True, type=Path, help="New CSV path; never overwritten")
    args = parser.parse_args(argv)
    try:
        scope = SelectedIncidents(
            host=args.instance, tenant_scope=args.tenant, sys_ids=tuple(args.ids),
            consented=args.consent_selected_read, grant_current=True,
        )
        staged = read_selected_once(
            scope, grant_file=args.grant_file,
            token=os.environ.get("OWG_SERVICENOW_TOKEN"), since=args.since,
            opener=opener,
        )
        content, report = project_csv(scope, staged, timezone_name=args.timezone)
        _publish_new(args.output, content, grant_file=args.grant_file, scope=scope)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (SyncError, StagingError, OSError, ValueError, OverflowError) as exc:
        # Never echo provider rows, bearer tokens, paths or user-supplied IDs.
        print(json.dumps({"status": "refused", "reason": "Check selected scope, consent, grant, source response, timezone and new output path"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
