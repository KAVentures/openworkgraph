"""Explicitly consented Microsoft Graph Outlook message metadata -> OWG CSV.

Reads only 1-8 exact operator-selected immutable message IDs. No background
sync, message content, provider write, implicit import or checkpoint.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
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
from urllib import error, parse, request

FORMAT = "openworkgraph.m365-outlook-selected.v1"
_ID = re.compile(r"[A-Za-z0-9_+=/-]{1,512}\Z", re.ASCII)
_LABEL = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z", re.ASCII)
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+={0,3}\Z", re.ASCII)
_KEYS = {"version", "enabled", "consented", "tenant_scope", "mailbox_scope", "message_ids"}
_FIELDS = {"id", "receivedDateTime", "lastModifiedDateTime", "isRead"}
_MAX_BYTES = 16384


class OutlookError(ValueError):
    """Minimized refusal; never include source IDs, tokens or message content."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    return request.build_opener(
        request.ProxyHandler({}),
        request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OutlookError("Duplicate JSON key")
        result[key] = value
    return result


def _scope(tenant, mailbox, ids):
    if (type(tenant) is not str or not _LABEL.fullmatch(tenant)
            or type(mailbox) is not str or not _LABEL.fullmatch(mailbox)
            or type(ids) is not list or not 1 <= len(ids) <= 8
            or any(type(i) is not str or not _ID.fullmatch(i) for i in ids)
            or len(set(ids)) != len(ids)):
        raise OutlookError("Invalid selected Outlook scope")
    return hashlib.sha256((tenant + "\0" + mailbox).encode("ascii")).hexdigest()


def _grant(path, tenant, mailbox, ids):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 8192
                    or (os.name == "posix" and info.st_mode & 0o077)):
                raise OutlookError("Invalid owner-only Outlook grant")
            raw = stream.read(8193)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise OutlookError("Outlook grant missing, revoked or invalid") from None
    if (type(value) is not dict or set(value) != _KEYS
            or type(value["version"]) is not int or value["version"] != 1
            or value["enabled"] is not True or value["consented"] is not True
            or value["tenant_scope"] != tenant or value["mailbox_scope"] != mailbox
            or type(value["message_ids"]) is not list
            or len(value["message_ids"]) != len(ids)
            or any(type(x) is not str for x in value["message_ids"])
            or sorted(value["message_ids"]) != sorted(ids)):
        raise OutlookError("Outlook grant missing, revoked or mismatched")


def _instant(value):
    if type(value) is not str or len(value) > 40:
        raise OutlookError("Invalid Graph timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Missing UTC offset")
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise OutlookError("Invalid Graph timestamp") from None


def _project(raw, selected_id):
    if type(raw) is not bytes or len(raw) > _MAX_BYTES:
        raise OutlookError("Oversized Graph response")
    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise OutlookError("Invalid Graph JSON") from None
    if (type(record) is not dict or not _FIELDS.issubset(record)
            or set(record) - _FIELDS - {"@odata.context", "@odata.etag"}
            or type(record["id"]) is not str or record["id"] != selected_id
            or type(record["isRead"]) is not bool):
        raise OutlookError("Graph selected message identity or fields mismatch")
    received = _instant(record["receivedDateTime"])
    modified = _instant(record["lastModifiedDateTime"])
    if modified < received:
        raise OutlookError("Graph modification precedes receipt")
    return received, modified, record["isRead"]


def _private_parent(parent):
    if parent.is_symlink() or not parent.is_dir():
        raise OutlookError("Private output directory required")
    info = parent.stat()
    if os.name == "posix" and info.st_mode & 0o077:
        raise OutlookError("Output directory must be owner-only")


def _publish(path, content, grant, tenant, mailbox, ids):
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".owg-outlook-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _grant(grant, tenant, mailbox, ids)
        os.link(tmp, path)  # create-only; never replace existing output
    except OSError:
        raise OutlookError("Could not create private Outlook export") from None
    finally:
        if tmp is not None:
            os.unlink(tmp)


def export_selected(*, tenant, mailbox, message_ids, grant_file: Path,
                    output: Path, token, consent_read=False, consent_export=False,
                    opener=None):
    if consent_read is not True or consent_export is not True:
        raise OutlookError("Separate provider read and local export consents required")
    namespace = _scope(tenant, mailbox, message_ids)
    if (type(token) is not str or not 1 <= len(token) <= 4096
            or not _TOKEN.fullmatch(token)):
        raise OutlookError("Invalid Graph bearer credential")
    if os.path.abspath(grant_file) == os.path.abspath(output):
        raise OutlookError("Grant and export paths must be distinct")
    _private_parent(output.parent)
    if output.exists() or output.is_symlink():
        raise OutlookError("Outlook export must be new")
    _grant(grant_file, tenant, mailbox, message_ids)
    transport = _opener() if opener is None else opener
    rows = []
    for message_id in sorted(message_ids):
        _grant(grant_file, tenant, mailbox, message_ids)
        url = ("https://graph.microsoft.com/v1.0/me/messages/"
               + parse.quote(message_id, safe="")
               + "?%24select=id%2CreceivedDateTime%2ClastModifiedDateTime%2CisRead")
        req = request.Request(url, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Prefer": 'IdType="ImmutableId"',
        }, method="GET")
        try:
            with transport.open(req, timeout=15) as response:
                status = response.status
                final_url = response.geturl()
                media = response.headers.get("Content-Type", "")
                raw = response.read(_MAX_BYTES + 1)
        except (OSError, error.URLError, ValueError):
            raise OutlookError("Selected Graph read failed") from None
        if (type(status) is not int or status != 200 or final_url != url
                or type(media) is not str or len(media) > 160
                or not re.fullmatch(r"application/json(?:\s*;[^;\r\n]+)*", media, re.I)
                or re.search(r"charset\s*=\s*(?!utf-8(?:;|$))", media, re.I)):
            raise OutlookError("Unexpected Graph transport response")
        received, modified, is_read = _project(raw, message_id)
        _grant(grant_file, tenant, mailbox, message_ids)
        identity = namespace + "\0" + message_id
        case_id = "outlookcase:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        revision = identity + "\0" + modified + "\0" + str(is_read)
        event_id = "outlookevent:" + hashlib.sha256(revision.encode("utf-8")).hexdigest()
        rows.append((case_id, "Observed read status: " + ("read" if is_read else "unread"),
                     modified, event_id))
    # Never publish partial batches. A final grant check also gates publication.
    csv_file = io.StringIO(newline="")
    writer = csv.writer(csv_file, lineterminator="\n")
    writer.writerow(("case_id", "activity", "timestamp", "source_event_id"))
    writer.writerows(sorted(rows))
    _publish(output, csv_file.getvalue(), grant_file, tenant, mailbox, message_ids)
    return {
        "format": FORMAT, "status": "exported",
        "selected_messages": len(message_ids), "source_snapshots": len(rows),
        "provider_get_requests": len(rows), "source_writes_performed": False,
        "automatic_owg_append": False, "checkpoint_advanced": False,
        "provider_tenant_verified": False, "historical_completeness": "unknown",
        "message_content_retained": False,
    }


def main(argv=None, *, opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--mailbox", required=True)
    parser.add_argument("--message-id", dest="ids", action="append", required=True)
    parser.add_argument("--grant-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--consent-selected-read", action="store_true")
    parser.add_argument("--consent-local-export", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = export_selected(
            tenant=args.tenant, mailbox=args.mailbox, message_ids=args.ids,
            grant_file=args.grant_file, output=args.output,
            token=os.environ.get("OWG_M365_GRAPH_TOKEN"),
            consent_read=args.consent_selected_read,
            consent_export=args.consent_local_export, opener=opener,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OutlookError, OSError, TypeError, ValueError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused",
            "reason": "Check exact selected scope, grant, token, consents and private new output",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
