"""Authenticated, bounded OWG source-key snapshot for uncertain M365 append review.

Read-only inspection; never authorizes retry or checkpoint movement.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from urllib import parse, request

from .m365_outlook_delta import _scope, _grant, _private_read, _delta_private_parent
from .m365_outlook_owg_receipt import ReceiptError, _unique, _target
from .m365_outlook_attempt_event_review import FORMAT as REVIEW_FORMAT
from .m365_outlook_verified_append_receipt import _exclusive_private
from .m365_outlook_resume_journal import _canonical
from .servicenow_owg_append import _LOG, _session, _opener
from .servicenow_owg_bootstrap import _inventory

FORMAT = "openworkgraph.m365-authenticated-membership-review.v1"
MAX_EXPORT = 16 * 1024 * 1024
MAX_ROWS = 100000


def _source_keys(raw: bytes, namespace: str):
    try:
        stream = io.StringIO(raw.decode("utf-8-sig"), newline="")
        reader = csv.reader(stream, strict=True)
        header = next(reader)
        expected = ["source_namespace", "source_event_id", "case_namespace",
                    "case_id", "activity", "timestamp", "resource", "attributes"]
        if header != expected:
            raise ReceiptError("Unexpected authenticated source CSV schema")
        keys = set()
        count = 0
        for row in reader:
            count += 1
            if count > MAX_ROWS or len(row) != len(expected):
                raise ReceiptError("Invalid or oversized authenticated source CSV")
            if row[0] != namespace or row[2] != namespace:
                raise ReceiptError("Authenticated export contains another source scope")
            if not row[1] or len(row[1]) > 512 or row[1] in keys:
                raise ReceiptError("Duplicate or invalid source identity")
            keys.add(row[1])
        return keys, count
    except (UnicodeError, csv.Error, StopIteration):
        raise ReceiptError("Invalid authenticated source CSV") from None


def inspect(*, tenant, mailbox, folder, grant_file: Path, review_file: Path,
            output: Path, log_id, confirm_log_id, port, session,
            consent_local_read=False, consent_private_export=False, opener=None):
    if consent_local_read is not True or consent_private_export is not True:
        raise ReceiptError("Separate read and private export consent required")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise ReceiptError("Exact target confirmation required")
    grant_file, review_file, output = map(Path, (grant_file, review_file, output))
    _delta_private_parent(output.parent)
    if (review_file.parent != output.parent or output == review_file
            or output == grant_file or output.exists() or output.is_symlink()
            or output.suffix != ".json" or not output.name.startswith("authenticated-")
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                   for c in output.name)):
        raise ReceiptError("Unsafe private output path")
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    raw_review = _private_read(review_file, 131072)
    try:
        review = json.loads(raw_review, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, TypeError):
        raise ReceiptError("Invalid private review") from None
    if (type(review) is not dict or review.get("format") != REVIEW_FORMAT
            or review.get("scope_sha256") != scope or review.get("log_id") != log_id
            or review.get("status") != "requires_independent_event_membership_review"
            or review.get("event_membership_verified") is not False
            or review.get("safe_to_retry") is not False
            or review.get("checkpoint_advanced") is not False):
        raise ReceiptError("Private review scope mismatch")
    expected = review.get("event_ids")
    if (type(expected) is not list or len(expected) > 200
            or len(set(map(str, expected))) != len(expected)
            or any(type(x) is not str or not x.startswith("outlookevent:")
                   or len(x) > 512 for x in expected)):
        raise ReceiptError("Invalid expected event identities")
    session = _session(session)
    transport = _opener() if opener is None else opener
    namespace = "m365_" + scope[:32]
    before = _target(_inventory(transport, port, session), log_id, namespace)
    _grant(grant_file, tenant, mailbox, folder)
    url = (f"http://127.0.0.1:{port}/v1/event-logs/{log_id}/export?"
           + parse.urlencode({"format": "sourcecsv", "case_type": "source_case"}))
    req = request.Request(url, method="GET", headers={
        "Authorization": "OWG-Session " + session, "Accept": "text/csv"})
    try:
        with transport.open(req, timeout=15) as response:
            if response.status != 200:
                raise ReceiptError("Authenticated source export rejected")
            raw = response.read(MAX_EXPORT + 1)
    except (OSError, TimeoutError):
        raise ReceiptError("Authenticated source export unavailable") from None
    if len(raw) > MAX_EXPORT:
        raise ReceiptError("Authenticated source export too large")
    keys, count = _source_keys(raw, namespace)
    if count != before:
        raise ReceiptError("Export and inventory count mismatch")
    _grant(grant_file, tenant, mailbox, folder)
    after = _target(_inventory(transport, port, session), log_id, namespace)
    if after != before:
        raise ReceiptError("Inventory changed during source review")
    matched = sorted(set(expected) & keys)
    missing = sorted(set(expected) - keys)
    report = {
        "format": FORMAT, "status": "authenticated_snapshot_review_only",
        "scope_sha256": scope, "log_id": log_id,
        "review_sha256": hashlib.sha256(raw_review).hexdigest(),
        "authenticated_export_sha256": hashlib.sha256(raw).hexdigest(),
        "inventory_events": before, "source_events": len(expected),
        "matched_event_ids": matched, "missing_event_ids": missing,
        "authenticated_local_session": True, "event_membership_verified": False,
        "safe_to_retry": False, "checkpoint_advanced": False,
        "owg_write_requests": 0, "provider_requests": 0,
    }
    _grant(grant_file, tenant, mailbox, folder)
    try:
        _exclusive_private(output, _canonical(report) + b"\n")
    except FileExistsError:
        raise ReceiptError("Report already exists") from None
    return {"format": FORMAT, "status": report["status"],
            "matched": len(matched), "missing": len(missing),
            "safe_to_retry": False, "checkpoint_advanced": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        p.add_argument("--" + key, required=True)
    for key in ("grant-file", "review-file", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--consent-local-read", action="store_true")
    p.add_argument("--consent-private-export", action="store_true")
    a = p.parse_args(argv)
    try:
        result = inspect(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            grant_file=a.grant_file, review_file=a.review_file, output=a.output,
            log_id=a.log_id, confirm_log_id=a.confirm_log_id, port=a.port,
            session=os.environ.get("OWG_DASHBOARD_SESSION"),
            consent_local_read=a.consent_local_read,
            consent_private_export=a.consent_private_export)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReceiptError, ValueError, TypeError, OSError):
        print(json.dumps({"format": FORMAT, "status": "refused", "safe_to_retry": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
