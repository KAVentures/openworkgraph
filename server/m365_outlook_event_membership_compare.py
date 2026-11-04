"""Review exact M365 source identities against a separately exported local OWG CSV.

Offline comparison only: exported files are not authenticated membership evidence.
No network, provider write, OWG write, retry authority or checkpoint changes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path

from .m365_outlook_delta import _scope, _grant, _private_read, _delta_private_parent
from .m365_outlook_owg_receipt import ReceiptError, _unique
from .m365_outlook_attempt_event_review import FORMAT as REVIEW_FORMAT
from .m365_outlook_resume_journal import _canonical
from .m365_outlook_verified_append_receipt import _exclusive_private

FORMAT = "openworkgraph.m365-event-membership-comparison.v1"
MAX_EXPORT = 16 * 1024 * 1024
MAX_EVENTS = 100000


def compare(*, tenant, mailbox, folder, grant_file: Path, review_file: Path,
            export_file: Path, output: Path, log_id, confirm_log_id,
            consent_local_read=False, consent_private_export=False):
    if consent_local_read is not True or consent_private_export is not True:
        raise ReceiptError("Separate local read and private report consent required")
    from .servicenow_owg_append import _LOG
    if not isinstance(log_id, str) or not _LOG.fullmatch(log_id) or confirm_log_id != log_id:
        raise ReceiptError("Exact log confirmation required")
    grant_file, review_file, export_file, output = map(
        Path, (grant_file, review_file, export_file, output))
    _delta_private_parent(output.parent)
    if (any(p.parent != output.parent for p in (review_file, export_file))
            or len({str(p.absolute()) for p in (grant_file, review_file, export_file, output)}) != 4
            or output.exists() or output.is_symlink()
            or output.suffix != ".json" or not output.name.startswith("comparison-")
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                   for c in output.name)):
        raise ReceiptError("Unsafe private comparison paths")
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    try:
        review = json.loads(_private_read(review_file, 131072), object_pairs_hook=_unique)
    except (ValueError, TypeError, UnicodeError):
        raise ReceiptError("Invalid review manifest") from None
    if (type(review) is not dict or review.get("format") != REVIEW_FORMAT
            or review.get("scope_sha256") != scope or review.get("log_id") != log_id
            or review.get("status") != "requires_independent_event_membership_review"
            or review.get("event_membership_verified") is not False
            or review.get("safe_to_retry") is not False
            or review.get("checkpoint_advanced") is not False):
        raise ReceiptError("Review scope or status mismatch")
    expected = review.get("event_ids")
    if (type(expected) is not list or len(expected) > 200
            or any(type(x) is not str or not x.startswith("outlookevent:")
                   or len(x) > 512 for x in expected)
            or len(set(expected)) != len(expected)):
        raise ReceiptError("Invalid source identities")
    raw = _private_read(export_file, MAX_EXPORT)
    try:
        rows = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = next(rows)
        if len(header) != len(set(header)) or "source_event_id" not in header:
            raise ReceiptError("Export requires a unique source_event_id column")
        col = header.index("source_event_id")
        observed = []
        for row in rows:
            if len(row) != len(header) or len(observed) >= MAX_EVENTS:
                raise ReceiptError("Invalid or oversized export")
            observed.append(row[col])
    except (UnicodeError, csv.Error, StopIteration):
        raise ReceiptError("Invalid export CSV") from None
    matches = set(expected).intersection(observed)
    duplicates = len(observed) != len(set(observed))
    # Duplicates and unscoped exports cannot prove completeness or absence.
    report = {
        "format": FORMAT, "status": "offline_comparison_only",
        "scope_sha256": scope, "log_id": log_id,
        "review_sha256": hashlib.sha256(_private_read(review_file, 131072)).hexdigest(),
        "export_sha256": hashlib.sha256(raw).hexdigest(),
        "source_events": len(expected), "export_rows": len(observed),
        "matching_source_events": len(matches),
        "unmatched_source_events": sorted(set(expected) - matches),
        "duplicate_export_ids": duplicates,
        "export_scope_authenticated": False,
        "event_membership_verified": False,
        "safe_to_retry": False, "checkpoint_advanced": False,
        "provider_requests": 0, "owg_write_requests": 0,
    }
    _grant(grant_file, tenant, mailbox, folder)
    try:
        _exclusive_private(output, _canonical(report) + b"\\n")
    except FileExistsError:
        raise ReceiptError("Comparison report already exists") from None
    return {k: report[k] for k in (
        "format", "status", "source_events", "export_rows",
        "matching_source_events", "duplicate_export_ids",
        "event_membership_verified", "safe_to_retry")}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        p.add_argument("--" + n, required=True)
    for n in ("grant-file", "review-file", "export-file", "output"):
        p.add_argument("--" + n, type=Path, required=True)
    p.add_argument("--consent-local-read", action="store_true")
    p.add_argument("--consent-private-export", action="store_true")
    a = p.parse_args(argv)
    try:
        print(json.dumps(compare(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            grant_file=a.grant_file, review_file=a.review_file,
            export_file=a.export_file, output=a.output,
            log_id=a.log_id, confirm_log_id=a.confirm_log_id,
            consent_local_read=a.consent_local_read,
            consent_private_export=a.consent_private_export)))
        return 0
    except (ReceiptError, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"format": FORMAT, "status": "refused", "safe_to_retry": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
