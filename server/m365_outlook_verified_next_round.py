"""Explicit next Microsoft Graph delta read gated by a previously persisted OWG handoff.

No autonomous sync, source writes, OWG append or provider checkpoint commit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from .m365_outlook_delta import (
    DeltaError, MAX_CANDIDATE_BYTES, _delta_private_parent, _private_read,
    export_delta,
)
from .m365_outlook_owg_receipt import ReceiptError
from .m365_outlook_resume_handoff import prepare_handoff
from .m365_outlook_resume_journal import _canonical

FORMAT = "openworkgraph.m365-verified-next-round.v1"


def run_verified_next_round(*, tenant, mailbox, folder, log_id, confirm_log_id,
                            grant_file: Path, previous_candidate: Path,
                            previous_csv: Path, receipt_file: Path,
                            journal_dir: Path, handoff_dir: Path,
                            successor_label: str, output: Path, candidate: Path,
                            token: str, port=8765, session=None,
                            consent_local_read=False, confirm_removals_reviewed=False,
                            consent_delta_read=False, consent_local_export=False,
                            opener=None):
    """Check existing handoff and live OWG state before bounded provider reads."""
    if not all((consent_local_read is True, confirm_removals_reviewed is True,
                consent_delta_read is True, consent_local_export is True)):
        raise ReceiptError("Separate local and provider read/export approvals required")
    previous_candidate = Path(previous_candidate)
    journal_dir, handoff_dir = Path(journal_dir), Path(handoff_dir)
    output, candidate = Path(output), Path(candidate)
    _delta_private_parent(handoff_dir)
    _delta_private_parent(previous_candidate.parent)
    if output == previous_csv or candidate == previous_candidate:
        raise ReceiptError("Next-round artifacts must be distinct")
    # A handoff must already exist. Never create one as a side effect of the read.
    try:
        prior = json.loads(_private_read(previous_candidate, MAX_CANDIDATE_BYTES))
        prior_sha = prior["csv_sha256"]
        if (type(prior_sha) is not str or len(prior_sha) != 64
                or any(c not in "0123456789abcdef" for c in prior_sha)):
            raise ValueError("Invalid prior candidate digest")
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ReceiptError("Prior candidate invalid") from None
    identity = hashlib.sha256(_canonical({
        "candidate_sha256": prior_sha, "log_id": log_id,
    })).hexdigest()
    handoff = handoff_dir / ("handoff-" + identity + ".json")
    _private_read(handoff, 8192)
    decision = prepare_handoff(
        journal_dir=journal_dir, handoff_dir=handoff_dir,
        successor_label=successor_label,
        tenant=tenant, mailbox=mailbox, folder=folder,
        log_id=log_id, confirm_log_id=confirm_log_id,
        grant_file=Path(grant_file), candidate=previous_candidate,
        csv_path=Path(previous_csv), receipt_file=Path(receipt_file),
        port=port, session=session,
        consent_local_read=True, confirm_removals_reviewed=True,
    )
    if decision.get("idempotent_replay") is not True:
        raise ReceiptError("Previously persisted handoff required")
    # Existing export_delta checks current grant before every Graph request,
    # validates complete bounded delta pages and creates artifacts exclusively.
    result = export_delta(
        tenant=tenant, mailbox=mailbox, folder_id=folder,
        grant_file=Path(grant_file), output=output, candidate=candidate,
        token=token, previous_candidate=previous_candidate,
        confirm_previous_import=True, consent_read=True,
        consent_export=True, opener=opener,
    )
    return {
        "format": FORMAT, "status": "next_round_review_ready",
        "verified_prior_handoff": True,
        "provider_get_requests": result["provider_get_requests"],
        "event_snapshots": result["event_snapshots"],
        "removal_markers_for_review": result["removal_markers_for_review"],
        "checkpoint_advanced": False, "automatic_owg_append": False,
        "real_provider_acceptance": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id",
                "successor-label"):
        parser.add_argument("--" + key, required=True)
    for key in ("grant-file", "previous-candidate", "previous-csv", "receipt-file",
                "journal-dir", "handoff-dir", "output", "candidate"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    for key in ("consent-local-read", "confirm-removals-reviewed",
                "consent-delta-read", "consent-local-export"):
        parser.add_argument("--" + key, action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_verified_next_round(
            tenant=args.tenant, mailbox=args.mailbox, folder=args.folder,
            log_id=args.log_id, confirm_log_id=args.confirm_log_id,
            successor_label=args.successor_label, grant_file=args.grant_file,
            previous_candidate=args.previous_candidate, previous_csv=args.previous_csv,
            receipt_file=args.receipt_file, journal_dir=args.journal_dir,
            handoff_dir=args.handoff_dir, output=args.output, candidate=args.candidate,
            token=os.environ.get("OWG_M365_GRAPH_TOKEN", ""),
            session=os.environ.get("OWG_DASHBOARD_SESSION"), port=args.port,
            consent_local_read=args.consent_local_read,
            confirm_removals_reviewed=args.confirm_removals_reviewed,
            consent_delta_read=args.consent_delta_read,
            consent_local_export=args.consent_local_export,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "checkpoint_advanced": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
