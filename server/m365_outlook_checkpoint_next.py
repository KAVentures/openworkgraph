"""Opt-in next Graph delta read anchored to a durably committed OWG checkpoint.

No implicit append, checkpoint advancement, or provider retry. A previously
reviewed and committed candidate is the only permitted predecessor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .m365_outlook_checkpoint import inspect_checkpoint
from .m365_outlook_delta import _candidate, _private_read, _scope, MAX_CANDIDATE_BYTES
from .m365_outlook_owg_receipt import ReceiptError
from .m365_outlook_verified_next_round import run_verified_next_round

FORMAT = "openworkgraph.m365-checkpoint-next.v1"


def extract_from_checkpoint(*, tenant, mailbox, folder, log_id, confirm_log_id,
                            grant_file: Path, ledger_dir: Path,
                            previous_candidate: Path, previous_csv: Path,
                            receipt_file: Path, journal_dir: Path,
                            handoff_dir: Path, successor_label: str,
                            output: Path, candidate: Path, token: str,
                            port=8765, session=None,
                            consent_local_read=False, consent_delta_read=False,
                            consent_local_export=False,
                            confirm_removals_reviewed=False,
                            opener=None, membership_opener=None):
    if not all((consent_local_read is True, consent_delta_read is True,
                consent_local_export is True, confirm_removals_reviewed is True)):
        raise ReceiptError("Explicit local, provider and removal review consent required")
    if type(log_id) is not str or not log_id or log_id != confirm_log_id:
        raise ReceiptError("Log identity not confirmed")
    previous_candidate = Path(previous_candidate)
    previous_csv = Path(previous_csv)
    entry = inspect_checkpoint(
        tenant=tenant, mailbox=mailbox, folder=folder,
        ledger_dir=Path(ledger_dir), grant_file=Path(grant_file),
        consent_local_read=True)
    if entry is None or entry["log_id"] != log_id:
        raise ReceiptError("No committed predecessor for this log")
    scope = _scope(tenant, mailbox, folder)
    cursor = _candidate(previous_candidate, scope, folder, True)
    raw = json.loads(_private_read(previous_candidate, MAX_CANDIDATE_BYTES))
    if (entry["cursor_url"] != cursor
            or entry["candidate_sha256"] != raw["csv_sha256"]
            or previous_csv.resolve() !=
                (previous_candidate.parent / raw["csv_name"]).resolve()):
        raise ReceiptError("Previous candidate does not match committed checkpoint")
    # Refuse a different candidate after the checkpoint inspection; the
    # verified delegate independently validates prior handoff, OWG receipt,
    # live membership and provider grant before any bounded Graph GET.
    result = run_verified_next_round(
        tenant=tenant, mailbox=mailbox, folder=folder, log_id=log_id,
        confirm_log_id=confirm_log_id, grant_file=Path(grant_file),
        previous_candidate=previous_candidate, previous_csv=previous_csv,
        receipt_file=Path(receipt_file), journal_dir=Path(journal_dir),
        handoff_dir=Path(handoff_dir), successor_label=successor_label,
        output=Path(output), candidate=Path(candidate), token=token,
        port=port, session=session, consent_local_read=True,
        confirm_removals_reviewed=True, consent_delta_read=True,
        consent_local_export=True, require_event_membership=True,
        opener=opener, membership_opener=membership_opener)
    if result.get("status") != "next_round_review_ready":
        raise ReceiptError("Next round not review ready")
    return {"format": FORMAT, "status": "review_required",
            "committed_predecessor_generation": entry["generation"],
            "event_snapshots": result["event_snapshots"],
            "removal_markers_for_review": result["removal_markers_for_review"],
            "checkpoint_advanced": False, "owg_append_performed": False,
            "automatic_retry_allowed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id",
                "successor-label"):
        parser.add_argument("--" + key, required=True)
    for key in ("grant-file", "ledger-dir", "previous-candidate", "previous-csv",
                "receipt-file", "journal-dir", "handoff-dir", "output", "candidate"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    for key in ("consent-local-read", "consent-delta-read",
                "consent-local-export", "confirm-removals-reviewed"):
        parser.add_argument("--" + key, action="store_true")
    args = vars(parser.parse_args(argv))
    try:
        result = extract_from_checkpoint(
            **args, token=os.environ.get("OWG_M365_GRAPH_TOKEN", ""),
            session=os.environ.get("OWG_DASHBOARD_SESSION"))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReceiptError, ValueError, TypeError, KeyError, OSError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "checkpoint_advanced": False,
                          "automatic_retry_allowed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
