"""Consent-gated, bounded Salesforce CaseHistory HTTPS read to local OWG CSV.

Only selected CaseHistory creation/status observations are exported. No Salesforce
business writes, automatic polling, checkpoint, provider identity proof or history
completeness is implied.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib import error, parse, request

from scripts.salesforce_case_history import ID, ExportError, project

FORMAT = "openworkgraph.salesforce-live-case-history.v1"
MAX_CASES = 8
MAX_RECORDS = 200
MAX_PAGES = 8
MAX_RESPONSE_BYTES = 262144
MAX_WINDOW = timedelta(days=31)
HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")
TOKEN = re.compile(r"[!-~]{16,8192}\Z")


class LiveHistoryError(ValueError):
    """Never include case identifiers, paths, source values or credentials in receipts."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise LiveHistoryError("Duplicate provider JSON key")
        obj[key] = value
    return obj


def _nonfinite(value):
    raise LiveHistoryError("Nonfinite provider JSON value")


def _instance(value, confirm_host, allow_loopback_http):
    if type(value) is not str or type(confirm_host) is not str:
        raise LiveHistoryError("Exact instance host confirmation required")
    parts = parse.urlsplit(value)
    host = parts.hostname
    if (parts.username is not None or parts.password is not None
            or parts.path or parts.query or parts.fragment
            or not host or host != confirm_host or host != host.lower()
            or not HOST.fullmatch(host)):
        raise LiveHistoryError("Invalid Salesforce instance scope")
    if (parts.scheme == "https" and parts.port is None
            and (host.endswith(".salesforce.com") or host.endswith(".force.com"))
            and host.count(".") >= 2):
        return value
    if (allow_loopback_http and parts.scheme == "http"
            and host in {"127.0.0.1", "localhost"}
            and parts.port is not None and 1 <= parts.port <= 65535):
        return value
    raise LiveHistoryError("Salesforce HTTPS instance required")


def _utc(value):
    if type(value) is not str or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise LiveHistoryError("Explicit second-precision UTC timestamp required")
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise LiveHistoryError("Invalid UTC bound") from None
    return dt


def _scope(instance, confirm_host, namespace, cases, since, until,
           token, output, allow_loopback_http):
    origin = _instance(instance, confirm_host, allow_loopback_http)
    if (type(namespace) is not str or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", namespace)):
        raise LiveHistoryError("Stable organization namespace required")
    if (type(cases) is not list or not 1 <= len(cases) <= MAX_CASES
            or any(type(c) is not str or ID.fullmatch(c) is None for c in cases)
            or len(set(cases)) != len(cases)):
        raise LiveHistoryError("One to eight distinct literal Case IDs required")
    lower, upper = _utc(since), _utc(until)
    if not lower < upper <= lower + MAX_WINDOW:
        raise LiveHistoryError("Bounded UTC window required")
    if type(token) is not str or TOKEN.fullmatch(token) is None:
        raise LiveHistoryError("Current read-only bearer required")
    if not isinstance(output, Path) or output.name in {"", ".", ".."}:
        raise LiveHistoryError("New private CSV path required")
    parent = output.parent
    try:
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid())
                or (os.name == "posix" and info.st_mode & 0o077)):
            raise LiveHistoryError("Private owner-only output directory required")
        if output.exists() or output.is_symlink():
            raise LiveHistoryError("Output already exists")
    except OSError:
        raise LiveHistoryError("Private output directory required") from None
    return origin


def _query(cases, since, until):
    # Case IDs and dates are validated literals, never caller-supplied SOQL.
    ids = ",".join("'" + c + "'" for c in cases)
    return (
        "SELECT Id,CaseId,CreatedDate,Field,NewValue FROM CaseHistory "
        "WHERE CaseId IN (" + ids + ") "
        "AND Field IN ('created','Status') "
        "AND CreatedDate >= " + since + " AND CreatedDate < " + until + " "
        "ORDER BY CreatedDate,Id LIMIT " + str(MAX_RECORDS + 1)
    )


def _fetch(opener, origin, bearer, cases, since, until, *, max_pages=1):
    """Follow at most eight strictly same-origin Salesforce query cursor pages."""
    url = origin + "/services/data/v60.0/query?" + parse.urlencode(
        {"q": _query(cases, since, until)})
    seen_urls = set()
    seen_ids = set()
    rows = []
    expected_total = None
    last_order = None
    for page in range(1, max_pages + 1):
        if url in seen_urls:
            raise LiveHistoryError("Repeated Salesforce query cursor")
        seen_urls.add(url)
        req = request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + bearer, "Accept": "application/json",
            "Cache-Control": "no-store",
        })
        try:
            with opener.open(req, timeout=10) as response:
                status, final = response.status, response.geturl()
                media = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, error.URLError, ValueError):
            raise LiveHistoryError("Salesforce read refused") from None
        if (status != 200 or final != url or media != "application/json"
                or len(raw) > MAX_RESPONSE_BYTES):
            raise LiveHistoryError("Salesforce response refused")
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                              parse_constant=_nonfinite)
        except (UnicodeError, ValueError, TypeError, RecursionError):
            raise LiveHistoryError("Invalid Salesforce JSON response") from None
        if (type(data) is not dict or type(data.get("done")) is not bool
                or type(data.get("totalSize")) is not int
                or not 0 <= data["totalSize"] <= MAX_RECORDS
                or type(data.get("records")) is not list
                or len(rows) + len(data["records"]) > MAX_RECORDS):
            raise LiveHistoryError("Incomplete or oversized Salesforce result")
        if expected_total is None:
            expected_total = data["totalSize"]
        elif expected_total != data["totalSize"]:
            raise LiveHistoryError("Salesforce result count changed between pages")
        if len(rows) + len(data["records"]) > expected_total:
            raise LiveHistoryError("Salesforce returned excess rows")
        for entry in data["records"]:
            if type(entry) is not dict:
                raise LiveHistoryError("Invalid Salesforce CaseHistory row")
            if not {"Id", "CaseId", "CreatedDate", "Field", "NewValue"} <= set(entry):
                raise LiveHistoryError("Missing Salesforce CaseHistory fields")
            if (type(entry["CaseId"]) is not str or entry["CaseId"] not in cases
                    or type(entry["Id"]) is not str or ID.fullmatch(entry["Id"]) is None
                    or entry["Id"] in seen_ids
                    or type(entry["CreatedDate"]) is not str
                    or type(entry["Field"]) is not str
                    or entry["Field"] not in {"created", "Status"}
                    or (entry["NewValue"] is not None
                        and type(entry["NewValue"]) is not str)):
                raise LiveHistoryError("Foreign, duplicate or invalid Salesforce history row")
            stamp = entry["CreatedDate"]
            try:
                moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if moment.tzinfo is None:
                    raise ValueError()
                moment = moment.astimezone(timezone.utc)
            except (ValueError, OverflowError):
                raise LiveHistoryError("Invalid source timestamp") from None
            if not _utc(since) <= moment < _utc(until):
                raise LiveHistoryError("Source row outside selected window")
            order = (moment, entry["Id"])
            if last_order is not None and order < last_order:
                raise LiveHistoryError("Salesforce page order changed")
            last_order = order
            seen_ids.add(entry["Id"])
            rows.append({key: (entry[key] if entry[key] is not None else "")
                         for key in ("Id", "CaseId", "CreatedDate", "Field", "NewValue")})
        if data["done"]:
            if "nextRecordsUrl" in data or len(rows) != expected_total:
                raise LiveHistoryError("Incomplete Salesforce query result")
            return rows, page
        cursor = data.get("nextRecordsUrl")
        # Salesforce query locator is an opaque relative path, never an arbitrary URL.
        if (type(cursor) is not str
                or not re.fullmatch(
                    r"/services/data/v60\.0/query/[A-Za-z0-9_-]{1,200}", cursor)
                or not data["records"] or page == max_pages):
            raise LiveHistoryError("Unfinished or unsafe Salesforce query cursor")
        url = origin + cursor
    raise LiveHistoryError("Salesforce query page limit reached")


def extract(instance, confirm_host, namespace, cases, since, until, token,
            output, *, allow_loopback_http=False, opener=None, max_pages=1):
    """Bounded real HTTPS read; private new CSV only, with counts-only receipt."""
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise LiveHistoryError("Explicit page bound must be 1–8")
    origin = _scope(instance, confirm_host, namespace, cases, since, until,
                    token, output, allow_loopback_http)
    transport = opener if opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())
    rows, pages = _fetch(transport, origin, token, cases, since, until,
                         max_pages=max_pages)
    if not rows:
        return {"format": FORMAT, "status": "no_selected_history",
                "source_rows": 0, "csv_created": False, "pages_fetched": pages,
                "source_completeness": "unknown", "source_writes_performed": False}
    raw_csv = io.StringIO(newline="")
    writer = csv.DictWriter(raw_csv,
                            fieldnames=["Id", "CaseId", "CreatedDate", "Field", "NewValue"],
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    try:
        projected, counts = project(raw_csv.getvalue().encode("utf-8"),
                                    account=namespace, cases=cases)
    except ExportError:
        raise LiveHistoryError("Salesforce history projection refused") from None
    # Exclusive creation prevents overwrites, symlink following and races.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(output, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(projected)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise LiveHistoryError("Private CSV creation refused") from None
    return {"format": FORMAT, "status": "exported", "csv_created": True,
            "source_rows": len(rows), "pages_fetched": pages,
            "events": counts["events"],
            "duplicate_rows": counts["duplicate_rows"],
            "represented_cases": counts["represented_cases"],
            "source_completeness": "unknown", "source_writes_performed": False}


def main(argv=None, *, opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--confirm-host", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--case", action="append", required=True, dest="cases")
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--consent-private-export", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true",
                        help="Disposable localhost acceptance only")
    args = parser.parse_args(argv)
    try:
        if not args.consent_selected_read or not args.consent_private_export:
            raise LiveHistoryError("Explicit source and local export consents required")
        result = extract(
            args.instance, args.confirm_host, args.namespace, args.cases,
            args.since, args.until, os.environ.get("OWG_SALESFORCE_READ_TOKEN"),
            args.output, allow_loopback_http=args.allow_loopback_http,
            opener=opener, max_pages=args.max_pages)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (LiveHistoryError, OSError, TypeError, ValueError, OverflowError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "reason": "Check consents, current read scope, bounds and private new output",
                          "source_writes_performed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
