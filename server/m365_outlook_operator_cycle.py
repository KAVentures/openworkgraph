"""Operator-controlled two-phase Microsoft Graph -> OWG synchronization.

Extract, append and handoff are separate invocations. An operator must review the
candidate and removal markers before invoking append. No checkpoint is written.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .m365_outlook_verified_next_round import run_verified_next_round
from .m365_outlook_verified_append_receipt import append_and_record
from .m365_outlook_resume_handoff import prepare_handoff
from .m365_outlook_owg_receipt import ReceiptError, UncertainReceipt
from .m365_outlook_delta import DeltaError

FORMAT = "openworkgraph.m365-operator-cycle.v1"


def extract(*, consent_local_read=False, consent_delta_read=False,
            consent_local_export=False, confirm_removals_reviewed=False, **kwargs):
    """Perform only the next bounded Graph GET and write review artifacts."""
    if not all((consent_local_read is True, consent_delta_read is True,
                consent_local_export is True,
                confirm_removals_reviewed is True)):
        raise ReceiptError("Explicit extraction and previous-removal review consent required")
    result = run_verified_next_round(
        consent_local_read=True, consent_delta_read=True,
        consent_local_export=True, confirm_removals_reviewed=True, **kwargs)
    if result.get("status") != "next_round_review_ready":
        raise ReceiptError("Extraction not ready for human review")
    return {"format": FORMAT, "phase": "extract", "status": "review_required",
            "event_snapshots": result["event_snapshots"],
            "removal_markers_for_review": result["removal_markers_for_review"],
            "checkpoint_advanced": False, "owg_append_performed": False}


def append(*, consent_local_read=False, consent_local_append=False,
           confirm_candidate_reviewed=False, **kwargs):
    """Append only a separately reviewed existing candidate, with durable latch."""
    if not all((consent_local_read is True, consent_local_append is True,
                confirm_candidate_reviewed is True)):
        raise ReceiptError("Separate read, append and candidate-review confirmation required")
    result = append_and_record(
        consent_local_read=True, consent_local_append=True, **kwargs)
    if result.get("status") != "receipt_persisted":
        raise UncertainReceipt("Receipt not durably persisted")
    return {"format": FORMAT, "phase": "append",
            "status": "receipt_persisted", "log_id": result["log_id"],
            "events_after": result["events_after"],
            "checkpoint_advanced": False, "automatic_retry": False}



def handoff(*, consent_local_read=False, confirm_removals_reviewed=False, **kwargs):
    """Verify saved OWG state and persist the separately approved successor handoff.

    This stage never contacts Graph or appends events. The production delegate
    checks grant, receipt and current authenticated OWG inventory before writing.
    """
    if not all((consent_local_read is True, confirm_removals_reviewed is True)):
        raise ReceiptError("Separate local read and removal review consent required")
    result = prepare_handoff(
        consent_local_read=True, confirm_removals_reviewed=True, **kwargs)
    if result.get("status") != "next_round_handoff_prepared":
        raise ReceiptError("Verified successor handoff not prepared")
    return {"format": FORMAT, "phase": "handoff",
            "status": "next_round_handoff_prepared",
            "idempotent_replay": result["idempotent_replay"],
            "checkpoint_advanced": False, "provider_requests": 0,
            "automatic_retry": False}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("extract", "append", "handoff"):
        p = sub.add_parser(phase)
        for flag in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
            p.add_argument("--" + flag, required=True)
        p.add_argument("--grant-file", type=Path, required=True)
        p.add_argument("--port", type=int, default=8765)
        p.add_argument("--consent-local-read", action="store_true")
        if phase == "extract":
            for flag in ("previous-candidate", "previous-csv", "receipt-file",
                         "journal-dir", "handoff-dir", "output", "candidate"):
                p.add_argument("--" + flag, type=Path, required=True)
            p.add_argument("--successor-label", required=True)
            for flag in ("confirm-removals-reviewed", "consent-delta-read",
                         "consent-local-export"):
                p.add_argument("--" + flag, action="store_true")
        elif phase == "append":
            for flag in ("candidate", "csv-path", "receipt-file"):
                p.add_argument("--" + flag, type=Path, required=True)
            p.add_argument("--consent-local-append", action="store_true")
            p.add_argument("--confirm-candidate-reviewed", action="store_true")
        else:
            for flag in ("candidate", "csv-path", "receipt-file",
                         "journal-dir", "handoff-dir"):
                p.add_argument("--" + flag, type=Path, required=True)
            p.add_argument("--successor-label", required=True)
            p.add_argument("--confirm-removals-reviewed", action="store_true")
    args = vars(parser.parse_args(argv))
    phase = args.pop("phase")
    args["log_id"] = args.pop("log_id")
    args["confirm_log_id"] = args.pop("confirm_log_id")
    args["session"] = os.environ.get("OWG_DASHBOARD_SESSION")
    try:
        if phase == "extract":
            args["token"] = os.environ.get("OWG_M365_GRAPH_TOKEN", "")
            result = extract(**args)
        elif phase == "append":
            result = append(**args)
        else:
            result = handoff(**args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except UncertainReceipt:
        print(json.dumps({"format": FORMAT, "phase": phase,
                          "status": "uncertain", "retry_allowed": False}, sort_keys=True))
        return 3
    except (ReceiptError, DeltaError, ValueError, TypeError, OSError, KeyError):
        print(json.dumps({"format": FORMAT, "phase": phase,
                          "status": "refused", "retry_allowed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
