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
from .m365_outlook_candidate_review import review as review_candidate
from .m365_outlook_recovery_triage import triage as recovery_triage
from .m365_outlook_event_membership import reconcile as reconcile_membership
from .m365_outlook_owg_receipt import ReceiptError, UncertainReceipt
from .m365_outlook_delta import DeltaError
from .m365_outlook_checkpoint import commit_checkpoint, inspect_checkpoint
from .m365_operator_audit_writer import record_operator_phase, AuditWriteError

FORMAT = "openworkgraph.m365-operator-cycle.v1"


def extract(*, consent_local_read=False, consent_delta_read=False,
            consent_local_export=False, confirm_removals_reviewed=False,
            require_event_membership=False, **kwargs):
    """Perform only the next bounded Graph GET and write review artifacts."""
    if not all((consent_local_read is True, consent_delta_read is True,
                consent_local_export is True,
                confirm_removals_reviewed is True)):
        raise ReceiptError("Explicit extraction and previous-removal review consent required")
    result = run_verified_next_round(
        consent_local_read=True, consent_delta_read=True,
        consent_local_export=True, confirm_removals_reviewed=True,
        require_event_membership=require_event_membership, **kwargs)
    if result.get("status") != "next_round_review_ready":
        raise ReceiptError("Extraction not ready for human review")
    return {"format": FORMAT, "phase": "extract", "status": "review_required",
            "event_snapshots": result["event_snapshots"],
            "removal_markers_for_review": result["removal_markers_for_review"],
            "checkpoint_advanced": False, "owg_append_performed": False}


def review(*, consent_local_read=False, **kwargs):
    """Inspect a scoped private delta candidate without contacting Graph or OWG."""
    if consent_local_read is not True:
        raise ReceiptError("Separate local review consent required")
    result = review_candidate(consent_local_read=True, **kwargs)
    if result.get("status") != "review_ready":
        raise ReceiptError("Candidate failed offline review")
    return {"format": FORMAT, "phase": "review", **result,
            "checkpoint_advanced": False, "provider_requests": 0,
            "owg_writes": 0}


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

def recover(*, consent_local_read=False, confirm_removals_reviewed=False, **kwargs):
    """Read-only authenticated recovery decision; never retries uncertain appends."""
    if consent_local_read is not True or confirm_removals_reviewed is not True:
        raise ReceiptError("Separate local read and removal review consent required")
    result = recovery_triage(
        consent_local_read=True, confirm_removals_reviewed=True, **kwargs)
    if result.get("status") not in (
            "receipt_verified", "handoff_verified",
            "manual_reconciliation_required"):
        raise ReceiptError("Recovery did not produce a recognized decision")
    return {"format": FORMAT, "phase": "recover", **result,
            "checkpoint_advanced": False, "automatic_retry_allowed": False}


def membership(*, consent_local_read=False, **kwargs):
    """Authenticate a bounded source-event membership readback without writes."""
    if consent_local_read is not True:
        raise ReceiptError("Separate local membership read consent required")
    result = reconcile_membership(consent_local_read=True, **kwargs)
    if result.get("status") not in ("all_candidate_events_present", "manual_reconciliation_required"):
        raise ReceiptError("Membership did not produce a recognized decision")
    return {"format": FORMAT, "phase": "membership", **result,
            "checkpoint_advanced": False, "automatic_retry_allowed": False}


def checkpoint_inspect(*, consent_local_read=False, **kwargs):
    """Inspect a scoped committed generation without exposing the private cursor."""
    entry = inspect_checkpoint(consent_local_read=consent_local_read, **kwargs)
    return {"format": FORMAT, "phase": "checkpoint-inspect",
            "status": "checkpoint_present" if entry else "empty",
            "generation": entry["generation"] if entry else 0,
            "provider_requests": 0, "automatic_retry_allowed": False}


def checkpoint_commit(*, consent_local_read=False,
                      confirm_removals_reviewed=False,
                      confirm_checkpoint_commit=False, **kwargs):
    """Explicit reviewed checkpoint transition; never initiates extraction."""
    result = commit_checkpoint(
        consent_local_read=consent_local_read,
        confirm_removals_reviewed=confirm_removals_reviewed,
        confirm_checkpoint_commit=confirm_checkpoint_commit, **kwargs)
    if result.get("status") != "checkpoint_committed":
        raise ReceiptError("Checkpoint not committed")
    return {"format": FORMAT, "phase": "checkpoint-commit", **result,
            "provider_requests": 0, "automatic_retry_allowed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("extract", "review", "append", "handoff", "recover", "membership", "checkpoint-inspect", "checkpoint-commit"):
        p = sub.add_parser(phase)
        for flag in ("tenant", "mailbox", "folder"):
            p.add_argument("--" + flag, required=True)
        if phase not in ("review", "checkpoint-inspect"):
            for flag in ("log-id", "confirm-log-id"):
                p.add_argument("--" + flag, required=True)
        p.add_argument("--grant-file", type=Path, required=True)
        if phase not in ("review", "checkpoint-inspect"):
            p.add_argument("--port", type=int, default=8765)
        p.add_argument("--consent-local-read", action="store_true")
        if phase.startswith("checkpoint-"):
            p.add_argument("--ledger-dir", type=Path, required=True)
        if phase == "checkpoint-commit":
            for flag in ("candidate", "csv-path", "receipt-file", "journal-dir", "handoff-dir"):
                p.add_argument("--" + flag, type=Path, required=True)
            p.add_argument("--successor-label", required=True)
            p.add_argument("--expected-previous-candidate-sha256")
            p.add_argument("--confirm-removals-reviewed", action="store_true")
            p.add_argument("--confirm-checkpoint-commit", action="store_true")
        elif phase == "extract":
            for flag in ("previous-candidate", "previous-csv", "receipt-file",
                         "journal-dir", "handoff-dir", "output", "candidate"):
                p.add_argument("--" + flag, type=Path, required=True)
            p.add_argument("--successor-label", required=True)
            p.add_argument("--require-event-membership", action="store_true",
                           help="Require fresh authenticated source-event membership before Graph GET")
            for flag in ("confirm-removals-reviewed", "consent-delta-read",
                         "consent-local-export"):
                p.add_argument("--" + flag, action="store_true")
        elif phase == "review":
            p.add_argument("--candidate", type=Path, required=True)
        elif phase == "checkpoint-inspect":
            # Ledger inspection requires no candidate or handoff artifacts.
            pass
        elif phase == "membership":
            for flag in ("candidate", "csv-path"):
                p.add_argument("--" + flag, type=Path, required=True)
        elif phase == "recover":
            for flag in ("candidate", "csv-path", "receipt-file"):
                p.add_argument("--" + flag, type=Path, required=True)
            for flag in ("journal-dir", "handoff-dir"):
                p.add_argument("--" + flag, type=Path)
            p.add_argument("--confirm-removals-reviewed", action="store_true")
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
    if phase not in ("review", "checkpoint-inspect"):
        args["session"] = os.environ.get("OWG_DASHBOARD_SESSION")
    try:
        if phase == "checkpoint-inspect":
            result = checkpoint_inspect(**args)
        elif phase == "checkpoint-commit":
            result = checkpoint_commit(**args)
        elif phase == "extract":
            args["token"] = os.environ.get("OWG_M365_GRAPH_TOKEN", "")
            result = extract(**args)
        elif phase == "review":
            result = review(**args)
        elif phase == "append":
            result = append(**args)
        elif phase == "recover":
            result = recover(**args)
        elif phase == "membership":
            result = membership(**args)
        else:
            result = handoff(**args)
        audit_dir = os.environ.get("OWG_M365_AUDIT_DIR", "")
        if audit_dir and phase in ("review", "extract", "append", "checkpoint-commit"):
            try:
                record_operator_phase(
                    phase, result, directory=Path(audit_dir),
                    tenant=args["tenant"], mailbox=args["mailbox"],
                    folder=args["folder"], log_id=args.get("log_id"))
            except AuditWriteError:
                # The underlying operation may already have committed. Never
                # encourage a retry when its audit outcome is uncertain.
                print(json.dumps({"format": FORMAT, "phase": phase,
                                  "status": "audit_uncertain",
                                  "retry_allowed": False}, sort_keys=True))
                return 3
        print(json.dumps(result, sort_keys=True))
        return 3 if phase in ("recover", "membership") and result["status"] == "manual_reconciliation_required" else 0
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
