"""Explicit Graph delta resume gate using a verified OWG append receipt.

This does not contact Graph or mutate a cursor. Count-only verification cannot
prove individual event membership; the operator must still review source state.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .m365_outlook_delta import _scope, _grant, _private_read, _delta_private_parent
from .m365_outlook_owg_receipt import (
    FORMAT as RECEIPT_FORMAT, ReceiptError, _artifact, _target, _unique,
)
from .servicenow_owg_append import _LOG, _session, _opener
from .servicenow_owg_bootstrap import _inventory

FORMAT = "openworkgraph.m365-outlook-resume-authorization.v1"


def authorize_resume(*, tenant, mailbox, folder, grant_file: Path,
                     candidate: Path, csv_path: Path, receipt_file: Path,
                     log_id, confirm_log_id, port, session,
                     consent_local_read=False, confirm_removals_reviewed=False,
                     opener=None):
    if consent_local_read is not True or confirm_removals_reviewed is not True:
        raise ReceiptError("Read consent and removal review required")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise ReceiptError("Exact target confirmation required")
    session = _session(session)
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    _, candidate_data = _artifact(candidate, csv_path, scope, folder)
    if receipt_file.parent != candidate.parent or receipt_file in (candidate, csv_path):
        raise ReceiptError("Receipt directory mismatch")
    _delta_private_parent(receipt_file.parent)
    try:
        receipt = json.loads(_private_read(receipt_file, 8192).decode("utf-8"),
                             object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ReceiptError("Invalid receipt") from None
    if (type(receipt) is not dict or receipt.get("format") != RECEIPT_FORMAT
            or receipt.get("status") not in ("verified", "verified_empty")
            or receipt.get("log_id") != log_id
            or receipt.get("csv_sha256") != candidate_data["csv_sha256"]
            or receipt.get("checkpoint_advanced") is not False
            or receipt.get("provider_requests") != 0
            or type(receipt.get("events_before")) is not int
            or type(receipt.get("events_after")) is not int
            or receipt["events_before"] < 0 or receipt["events_after"] < 0):
        raise ReceiptError("Receipt mismatch")
    if receipt["status"] == "verified_empty":
        if candidate_data["events"] != 0 or receipt["events_after"] != receipt["events_before"]:
            raise ReceiptError("Invalid empty round")
    else:
        if (type(receipt.get("added_events")) is not int
                or type(receipt.get("duplicate_events_skipped")) is not int
                or type(receipt.get("batch")) is not int
                or receipt["batch"] < 1 or receipt["added_events"] < 0
                or receipt["duplicate_events_skipped"] < 0
                or receipt["added_events"] + receipt["duplicate_events_skipped"] != candidate_data["events"]
                or receipt["events_after"] != receipt["events_before"] + receipt["added_events"]):
            raise ReceiptError("Receipt event counts mismatch")
    # Re-read current authenticated inventory, not merely the operator's JSON.
    transport = _opener() if opener is None else opener
    current = _target(_inventory(transport, port, session), log_id, "m365_" + scope[:32])
    _grant(grant_file, tenant, mailbox, folder)
    if current != receipt["events_after"]:
        raise ReceiptError("Target inventory changed since append")
    return {"format": FORMAT, "status": "resume_authorized_for_operator",
            "candidate_sha256": candidate_data["csv_sha256"],
            "log_id": log_id, "events_verified_by_count": candidate_data["events"],
            "removal_markers_reviewed_by_operator": True,
            "checkpoint_advanced": False, "provider_requests": 0,
            "event_level_proof": False}


def main(argv=None, *, opener=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        parser.add_argument("--" + flag, required=True)
    for flag in ("grant-file", "candidate", "csv-path", "receipt-file"):
        parser.add_argument("--" + flag, required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-local-read", action="store_true")
    parser.add_argument("--confirm-removals-reviewed", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = authorize_resume(
            tenant=args.tenant, mailbox=args.mailbox, folder=args.folder,
            grant_file=args.grant_file, candidate=args.candidate,
            csv_path=args.csv_path, receipt_file=args.receipt_file,
            log_id=args.log_id, confirm_log_id=args.confirm_log_id,
            port=args.port, session=os.environ.get("OWG_DASHBOARD_SESSION"),
            consent_local_read=args.consent_local_read,
            confirm_removals_reviewed=args.confirm_removals_reviewed,
            opener=opener)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "checkpoint_advanced": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
