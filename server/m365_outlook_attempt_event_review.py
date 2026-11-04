"""Prepare an owner-private event identity review for an uncertain M365 append.

This is NOT reconciliation: inventory totals cannot establish event membership.
Never POST, clear an attempt latch, advance a cursor, or authorize retry.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path

from .m365_outlook_delta import _delta_private_parent, _grant, _private_read, _scope
from .m365_outlook_owg_receipt import ReceiptError, _artifact, _target, _unique
from .m365_outlook_verified_append_receipt import FORMAT as ATTEMPT_FORMAT, _exclusive_private
from .m365_outlook_resume_journal import _canonical
from .servicenow_owg_append import _LOG, _opener, _session
from .servicenow_owg_bootstrap import _inventory

FORMAT = "openworkgraph.m365-event-review-manifest.v1"


def prepare_event_review(*, tenant, mailbox, folder, grant_file: Path,
                         candidate: Path, csv_path: Path, receipt_file: Path,
                         output: Path, log_id, confirm_log_id, port=8765,
                         session=None, consent_local_read=False,
                         consent_private_export=False, opener=None):
    if consent_local_read is not True or consent_private_export is not True:
        raise ReceiptError("Separate read and private export consent required")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise ReceiptError("Exact target confirmation required")
    grant_file, candidate, csv_path, receipt_file, output = map(
        Path, (grant_file, candidate, csv_path, receipt_file, output))
    _delta_private_parent(output.parent)
    if (not all(p.parent == output.parent for p in
                (candidate, csv_path, receipt_file))
            or output.suffix != ".json"
            or not output.name.startswith("review-")
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                   for c in output.name)
            or len({str(p.absolute()) for p in
                    (grant_file, candidate, csv_path, receipt_file, output)}) != 5
            or output.exists() or output.is_symlink()):
        raise ReceiptError("Invalid or occupied private review destination")
    if receipt_file.exists() or receipt_file.is_symlink():
        raise ReceiptError("Verified receipt exists; use the verified workflow")
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    body, artifact = _artifact(candidate, csv_path, scope, folder)
    latch = receipt_file.with_name(receipt_file.name + ".attempt")
    try:
        attempt = json.loads(_private_read(latch, 8192), object_pairs_hook=_unique)
    except (ValueError, TypeError, UnicodeError):
        raise ReceiptError("Invalid attempt latch") from None
    if (type(attempt) is not dict or attempt.get("format") != ATTEMPT_FORMAT
            or attempt.get("status") != "attempt_started"
            or attempt.get("log_id") != log_id
            or attempt.get("csv_sha256") != artifact["csv_sha256"]
            or attempt.get("automatic_retry") is not False
            or attempt.get("checkpoint_advanced") is not False):
        raise ReceiptError("Attempt and candidate do not match")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8")), strict=True))
    ids = [row[3] for row in rows[1:]]
    # _artifact already checks exact schema, row count, and unique IDs.
    if len(ids) != artifact["events"]:
        raise ReceiptError("Event identity count mismatch")
    authorized = _session(session)
    transport = _opener() if opener is None else opener
    before = _target(_inventory(transport, port, authorized), log_id,
                     "m365_" + scope[:32])
    _grant(grant_file, tenant, mailbox, folder)
    # Private file only. No source event IDs, mailbox or cursor on stdout.
    manifest = {
        "format": FORMAT, "status": "requires_independent_event_membership_review",
        "scope_sha256": scope, "log_id": log_id,
        "candidate_sha256": hashlib.sha256(_private_read(candidate, 32768)).hexdigest(),
        "csv_sha256": artifact["csv_sha256"],
        "event_ids": ids, "inventory_events_observed": before,
        "event_membership_verified": False, "safe_to_retry": False,
        "checkpoint_advanced": False, "provider_requests": 0,
        "owg_write_requests": 0,
    }
    _grant(grant_file, tenant, mailbox, folder)
    try:
        _exclusive_private(output, _canonical(manifest) + b"\n")
    except FileExistsError:
        raise ReceiptError("Review file already exists") from None
    return {"format": FORMAT, "status": "review_manifest_written",
            "event_count": len(ids), "event_membership_verified": False,
            "safe_to_retry": False, "checkpoint_advanced": False,
            "provider_requests": 0, "owg_write_requests": 0}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        p.add_argument("--" + name, required=True)
    for name in ("grant-file", "candidate", "csv-path", "receipt-file", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--consent-local-read", action="store_true")
    p.add_argument("--consent-private-export", action="store_true")
    a = p.parse_args(argv)
    try:
        print(json.dumps(prepare_event_review(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            grant_file=a.grant_file, candidate=a.candidate, csv_path=a.csv_path,
            receipt_file=a.receipt_file, output=a.output, log_id=a.log_id,
            confirm_log_id=a.confirm_log_id, port=a.port,
            session=os.environ.get("OWG_DASHBOARD_SESSION"),
            consent_local_read=a.consent_local_read,
            consent_private_export=a.consent_private_export)))
        return 0
    except (ReceiptError, ValueError, TypeError, OSError, KeyError, UnicodeError, csv.Error):
        print(json.dumps({"format": FORMAT, "status": "refused", "safe_to_retry": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
