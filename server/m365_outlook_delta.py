"""Bounded, operator-approved Microsoft Graph per-folder delta round.

Graph is read-only. A private CSV and SHA-bound candidate cursor are created
only for a *complete* round. Cursor use requires separate operator confirmation
of prior import; this command never appends to OWG or commits a checkpoint.
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
import stat
import sys
import tempfile
from urllib import error, parse, request

from .m365_outlook_selected import (
    _ID, _LABEL, _TOKEN, _instant, _opener, _pairs, _private_parent,
)

FORMAT = "openworkgraph.m365-outlook-delta.v1"
FIELDS = {"id", "receivedDateTime", "lastModifiedDateTime", "isRead"}
META = {"@odata.type", "@odata.etag"}
GRANT_KEYS = {"version", "enabled", "consented", "tenant_scope",
              "mailbox_scope", "folder_id"}
MAX_PAGES = 8
MAX_RECORDS = 200
MAX_PAGE_BYTES = 128 * 1024
MAX_CANDIDATE_BYTES = 32 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
TOKEN_KEYS = {"$skiptoken", "$deltatoken"}
SELECT = "id,receivedDateTime,lastModifiedDateTime,isRead"


class DeltaError(ValueError):
    """A non-sensitive refusal: never surface provider values or URLs."""


def _scope(tenant, mailbox, folder):
    if (type(tenant) is not str or not _LABEL.fullmatch(tenant)
            or type(mailbox) is not str or not _LABEL.fullmatch(mailbox)
            or type(folder) is not str or not _ID.fullmatch(folder)
            or folder in (".", "..")):
        raise DeltaError("Invalid folder scope")
    return hashlib.sha256((tenant + "\0" + mailbox + "\0" + folder).encode()).hexdigest()


def _private_read(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > limit
                    or (os.name == "posix" and info.st_mode & 0o077)):
                raise DeltaError("Private artifact invalid")
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise DeltaError("Private artifact oversized")
        return data
    except OSError:
        raise DeltaError("Private artifact unavailable") from None


def _grant(path, tenant, mailbox, folder):
    try:
        data = json.loads(_private_read(path, 8192).decode("utf-8"),
                          object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise DeltaError("Current delta grant invalid") from None
    if (type(data) is not dict or set(data) != GRANT_KEYS
            or type(data["version"]) is not int or data["version"] != 1
            or data["enabled"] is not True or data["consented"] is not True
            or data["tenant_scope"] != tenant or data["mailbox_scope"] != mailbox
            or data["folder_id"] != folder):
        raise DeltaError("Current delta grant revoked or mismatched")


def _initial(folder):
    return ("https://graph.microsoft.com/v1.0/me/mailFolders/"
            + parse.quote(folder, safe="")
            + "/messages/delta?%24select="
            + parse.quote(SELECT, safe="")
            + "&%24top=25")


def _valid_link(value, folder, kind):
    if (type(value) is not str or not 1 <= len(value) <= 8192
            or any(ord(c) < 33 or ord(c) > 126 or c == "\\" for c in value)):
        raise DeltaError("Unsafe Graph continuation")
    try:
        parts = parse.urlsplit(value)
        # Graph documents both /mailFolders/id and /mailfolders('id') forms.
        canonical = "/v1.0/me/mailFolders/" + parse.quote(folder, safe="") + "/messages/delta"
        parenthesized = ("/v1.0/me/mailfolders('"
                         + parse.quote(folder, safe="") + "')/messages/delta")
        if (parts.scheme != "https" or parts.netloc != "graph.microsoft.com"
                or parts.fragment or parts.path.lower() not in
                (canonical.lower(), parenthesized.lower())
                or not parts.query or len(parts.query) > 6000):
            raise DeltaError("Unsafe Graph continuation")
        pairs = parse.parse_qsl(parts.query, keep_blank_values=True,
                                strict_parsing=True, max_num_fields=4)
        if len(pairs) != len(set(k for k, _ in pairs)):
            raise DeltaError("Duplicate Graph cursor parameter")
        params = dict(pairs)
        expected = "$skiptoken" if kind == "next" else "$deltatoken"
        if (expected not in params or not 1 <= len(params[expected]) <= 4096
                or set(params) - {expected, "$select", "$top"}
                or ("$select" in params and params["$select"] != SELECT)
                or ("$top" in params and params["$top"] != "25")
                or any(any(ord(c) < 33 for c in v) for v in params.values())):
            raise DeltaError("Unsafe Graph cursor parameters")
    except (ValueError, UnicodeError):
        raise DeltaError("Unsafe Graph continuation") from None
    return value


def _parse_page(raw, folder):
    if len(raw) > MAX_PAGE_BYTES:
        raise DeltaError("Oversized Graph delta page")
    try:
        page = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise DeltaError("Invalid Graph delta JSON") from None
    if (type(page) is not dict or type(page.get("value")) is not list
            or len(page["value"]) > 25
            or set(page) - {"value", "@odata.context", "@odata.nextLink",
                            "@odata.deltaLink"}
            or ("@odata.nextLink" in page) == ("@odata.deltaLink" in page)):
        raise DeltaError("Incomplete or unexpected Graph delta page")
    if "@odata.context" in page and (type(page["@odata.context"]) is not str
            or len(page["@odata.context"]) > 1024):
        raise DeltaError("Invalid Graph context")
    kind = "next" if "@odata.nextLink" in page else "delta"
    cursor = _valid_link(page["@odata.nextLink" if kind == "next"
                               else "@odata.deltaLink"], folder, kind)
    return page["value"], kind, cursor


def _record(value, namespace):
    if type(value) is not dict or type(value.get("id")) is not str or not _ID.fullmatch(value["id"]):
        raise DeltaError("Invalid Graph message identity")
    identity = namespace + "\0" + value["id"]
    case = "outlookcase:" + hashlib.sha256(identity.encode()).hexdigest()
    if "@removed" in value:
        if (set(value) - {"id", "@removed", "@odata.type"}
                or type(value["@removed"]) is not dict
                or set(value["@removed"]) != {"reason"}
                or value["@removed"]["reason"] not in ("deleted", "changed")):
            raise DeltaError("Invalid Graph removal marker")
        return ("removed", case, value["id"], None)
    if (not FIELDS.issubset(value) or set(value) - FIELDS - META
            or type(value["isRead"]) is not bool):
        raise DeltaError("Unexpected Graph metadata fields")
    received = _instant(value["receivedDateTime"])
    modified = _instant(value["lastModifiedDateTime"])
    if modified < received:
        raise DeltaError("Invalid Graph message timestamps")
    revision = identity + "\0" + modified + "\0" + str(value["isRead"])
    event = "outlookevent:" + hashlib.sha256(revision.encode()).hexdigest()
    row = (case, "Observed read status: " + ("read" if value["isRead"] else "unread"),
           modified, event)
    return ("upsert", case, value["id"], row)


def _candidate(previous, scope, folder, confirmed):
    if previous is None:
        if confirmed:
            raise DeltaError("Previous import confirmation without candidate")
        return _initial(folder)
    if confirmed is not True:
        raise DeltaError("Explicit previous import confirmation required")
    _private_parent(previous.parent)
    try:
        value = json.loads(_private_read(previous, MAX_CANDIDATE_BYTES).decode("utf-8"),
                           object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise DeltaError("Invalid prior delta candidate") from None
    required = {"format", "scope_sha256", "csv_name", "csv_sha256",
                "cursor_url", "status", "events", "removals", "pages"}
    if (type(value) is not dict or set(value) != required
            or value["format"] != FORMAT or value["scope_sha256"] != scope
            or value["status"] != "candidate_requires_confirmed_import"
            or type(value["csv_name"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.csv", value["csv_name"])
            or type(value["csv_sha256"]) is not str or not SHA.fullmatch(value["csv_sha256"])
            or any(type(value[k]) is not int or value[k] < 0 for k in ("events", "removals", "pages"))
            or not 1 <= value["pages"] <= MAX_PAGES
            or value["events"] + value["removals"] > MAX_RECORDS):
        raise DeltaError("Prior delta candidate scope or schema mismatch")
    csv_path = previous.parent / value["csv_name"]
    if hashlib.sha256(_private_read(csv_path, 2 * 1024 * 1024)).hexdigest() != value["csv_sha256"]:
        raise DeltaError("Prior delta export changed")
    return _valid_link(value["cursor_url"], folder, "delta")


def _publish(path, data, grant_file, tenant, mailbox, folder):
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".owg-delta-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _grant(grant_file, tenant, mailbox, folder)
        os.link(tmp, path)
    except OSError:
        raise DeltaError("Could not create private delta artifact") from None
    finally:
        if tmp is not None:
            os.unlink(tmp)


def export_delta(*, tenant, mailbox, folder_id, grant_file: Path,
                 output: Path, candidate: Path, token,
                 consent_read=False, consent_export=False,
                 previous_candidate: Path | None = None,
                 confirm_previous_import=False, opener=None):
    if consent_read is not True or consent_export is not True:
        raise DeltaError("Separate Graph read and local export consents required")
    namespace = _scope(tenant, mailbox, folder_id)
    if (type(token) is not str or not 1 <= len(token) <= 4096
            or not _TOKEN.fullmatch(token)):
        raise DeltaError("Invalid Graph bearer")
    if (output.suffix != ".csv" or candidate.suffix != ".json"
            or output.parent != candidate.parent or output == candidate
            or any(os.path.abspath(p) == os.path.abspath(grant_file)
                   for p in (output, candidate))):
        raise DeltaError("Invalid private artifact paths")
    _private_parent(output.parent)
    if any(p.exists() or p.is_symlink() for p in (output, candidate)):
        raise DeltaError("Delta artifacts must be new")
    _grant(grant_file, tenant, mailbox, folder_id)
    url = _candidate(previous_candidate, namespace, folder_id, confirm_previous_import)
    transport = _opener() if opener is None else opener
    seen_urls = set()
    observations = {}
    removed = {}
    rows = {}
    pages = 0
    total = 0
    while True:
        if url in seen_urls or pages >= MAX_PAGES:
            raise DeltaError("Incomplete Graph delta round")
        seen_urls.add(url)
        _grant(grant_file, tenant, mailbox, folder_id)
        req = request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + token, "Accept": "application/json",
            "Prefer": 'IdType="ImmutableId", odata.maxpagesize=25',
        })
        try:
            with transport.open(req, timeout=15) as response:
                status = response.status
                final = response.geturl()
                media = response.headers.get("Content-Type", "")
                raw = response.read(MAX_PAGE_BYTES + 1)
        except (OSError, error.URLError, ValueError):
            raise DeltaError("Graph delta request refused") from None
        if (type(status) is not int or status != 200 or final != url
                or type(media) is not str or len(media) > 160
                or not re.fullmatch(r"application/json(?:\s*;[^;\r\n]+)*", media, re.I)
                or re.search(r"charset\s*=\s*(?!utf-8(?:;|$))", media, re.I)):
            raise DeltaError("Unexpected Graph delta transport")
        values, kind, cursor = _parse_page(raw, folder_id)
        pages += 1
        total += len(values)
        if total > MAX_RECORDS:
            raise DeltaError("Graph delta record budget exceeded")
        for value in values:
            kind_record, case, source_id, row = _record(value, namespace)
            if kind_record == "removed":
                if source_id in observations:
                    raise DeltaError("Conflicting Graph delta lifecycle")
                removed[source_id] = case
            else:
                if source_id in removed:
                    raise DeltaError("Conflicting Graph delta lifecycle")
                state_key = (source_id, row[2])
                if state_key in observations and observations[state_key] != row:
                    raise DeltaError("Conflicting Graph delta revision")
                observations[state_key] = row
                rows[row[3]] = row
        _grant(grant_file, tenant, mailbox, folder_id)
        if kind == "delta":
            break
        url = cursor
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer, lineterminator="\n")
    writer.writerow(("case_id", "activity", "timestamp", "source_event_id"))
    writer.writerows(sorted(rows.values()))
    csv_bytes = csv_buffer.getvalue().encode("utf-8")
    record = {
        "format": FORMAT, "scope_sha256": namespace,
        "csv_name": output.name, "csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
        "cursor_url": cursor, "status": "candidate_requires_confirmed_import",
        "events": len(rows), "removals": len(removed), "pages": pages,
    }
    # CSV first, then its SHA-bound candidate. Failure never advances a cursor.
    _publish(output, csv_bytes, grant_file, tenant, mailbox, folder_id)
    _publish(candidate, (json.dumps(record, sort_keys=True) + "\n").encode(),
             grant_file, tenant, mailbox, folder_id)
    return {
        "format": FORMAT, "status": "review_ready",
        "provider_get_requests": pages, "source_rows": total,
        "event_snapshots": len(rows), "removal_markers_for_review": len(removed),
        "complete_delta_round": True, "candidate_cursor_created": True,
        "checkpoint_advanced": False, "automatic_owg_append": False,
        "provider_tenant_verified": False, "historical_completeness": "unknown",
        "source_writes_performed": False, "message_content_retained": False,
    }


def main(argv=None, *, opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--mailbox", required=True)
    parser.add_argument("--folder-id", required=True)
    parser.add_argument("--grant-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--resume-candidate", type=Path)
    parser.add_argument("--confirm-previous-import", action="store_true")
    parser.add_argument("--consent-delta-read", action="store_true")
    parser.add_argument("--consent-local-export", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = export_delta(
            tenant=args.tenant, mailbox=args.mailbox, folder_id=args.folder_id,
            grant_file=args.grant_file, output=args.output, candidate=args.candidate,
            token=os.environ.get("OWG_M365_GRAPH_TOKEN"),
            consent_read=args.consent_delta_read,
            consent_export=args.consent_local_export,
            previous_candidate=args.resume_candidate,
            confirm_previous_import=args.confirm_previous_import, opener=opener,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (DeltaError, OSError, ValueError, TypeError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused",
            "reason": "Check exact folder grant, consents, private paths, prior import and Graph budget",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
