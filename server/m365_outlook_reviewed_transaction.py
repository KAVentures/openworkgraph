"""Explicit, single-invocation Microsoft 365 reviewed append and checkpoint flow.

This does not perform Graph extraction. The operator first runs extract and
review separately, inspects removal markers, and supplies the reviewed CSV
SHA-256 here. No automatic retry is permitted after any uncertain write.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import m365_outlook_operator_cycle as cycle
from .m365_outlook_owg_receipt import ReceiptError, UncertainReceipt
from .m365_operator_audit_writer import (preflight_operator_audit, record_operator_phase, AuditWriteError)
from .m365_outlook_delta import DeltaError
from .m365_outlook_checkpoint import inspect_checkpoint

FORMAT = "openworkgraph.m365-reviewed-transaction.v1"


def preflight(*, tenant, mailbox, folder, log_id, confirm_log_id,
              grant_file, candidate, ledger_dir, expected_review_csv_sha256,
              expected_previous_candidate_sha256=None, consent_local_read=False):
    """Read-only, grant-scoped review of the proposed checkpoint transition.

    No Graph call, OWG append, handoff, checkpoint write or audit receipt.
    The mutation path must revalidate everything; this is not a lock.
    """
    if consent_local_read is not True:
        raise ReceiptError("Explicit local read consent required")
    if not log_id or log_id != confirm_log_id:
        raise ReceiptError("Destination log confirmation mismatch")
    if (type(expected_review_csv_sha256) is not str
            or len(expected_review_csv_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_review_csv_sha256)):
        raise ReceiptError("Exact reviewed CSV SHA-256 required")
    scope = dict(tenant=tenant, mailbox=mailbox, folder=folder,
                 grant_file=Path(grant_file))
    # Preflight is read-only: reject a checkpoint advanced during review.
    initial_checkpoint = inspect_checkpoint(
        **scope, ledger_dir=Path(ledger_dir), consent_local_read=True)
    checked = cycle.review(candidate=Path(candidate), consent_local_read=True, **scope)
    if checked.get("csv_sha256") != expected_review_csv_sha256:
        raise ReceiptError("Candidate changed since explicit review")
    predecessor = inspect_checkpoint(
        **scope, ledger_dir=Path(ledger_dir), consent_local_read=True)
    if predecessor != initial_checkpoint:
        raise ReceiptError("Checkpoint changed during candidate review")
    if predecessor is None:
        if expected_previous_candidate_sha256 is not None:
            raise ReceiptError("Missing predecessor checkpoint")
        generation = 1
    else:
        if (type(expected_previous_candidate_sha256) is not str
                or predecessor["candidate_sha256"] != expected_previous_candidate_sha256
                or predecessor["log_id"] != log_id):
            raise ReceiptError("Stale or different predecessor checkpoint")
        generation = predecessor["generation"] + 1
    return {"format": FORMAT, "status": "review_ready_for_explicit_commit",
            "proposed_generation": generation,
            "reviewed_event_snapshots": checked.get("event_snapshots"),
            "removal_markers_for_review": checked.get("removal_markers_for_review"),
            "reviewed_csv_sha256": expected_review_csv_sha256,
            "checkpoint_predecessor_generation": generation - 1,
            "manual_review_and_separate_commit_required": True,
            "provider_requests": 0,
            "owg_writes": 0, "checkpoint_advanced": False,
            "automatic_retry_allowed": False}


def execute(*, tenant, mailbox, folder, log_id, confirm_log_id, grant_file,
            candidate, csv_path, receipt_file, journal_dir, handoff_dir,
            ledger_dir, successor_label, expected_review_csv_sha256,
            expected_previous_candidate_sha256=None, port=8765, session=None,
            consent_local_read=False, consent_local_append=False,
            confirm_candidate_reviewed=False, confirm_removals_reviewed=False,
            confirm_checkpoint_commit=False):
    """Require all approvals before the first OWG mutation.

    Each phase delegates to the production validators and authenticated
    adapters. Failures after append are not retried; the operator must use
    the existing recovery/membership/checkpoint inspection commands.
    """
    if not all((consent_local_read is True, consent_local_append is True,
                confirm_candidate_reviewed is True,
                confirm_removals_reviewed is True,
                confirm_checkpoint_commit is True)):
        raise ReceiptError("Independent read, append, removal and checkpoint approvals required")
    if log_id != confirm_log_id or not log_id:
        raise ReceiptError("Destination log confirmation mismatch")
    if (type(expected_review_csv_sha256) is not str
            or len(expected_review_csv_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_review_csv_sha256)):
        raise ReceiptError("Exact reviewed CSV SHA-256 required")
    scope = dict(tenant=tenant, mailbox=mailbox, folder=folder,
                 grant_file=Path(grant_file))
    audit_dir = os.environ.get("OWG_M365_AUDIT_DIR")
    if audit_dir:
        if os.name != "posix":
            raise ReceiptError("Audited mutation unsupported on this platform")
        preflight_operator_audit(Path(audit_dir))
    def audit(phase, result):
        if audit_dir:
            record_operator_phase(phase, result, directory=Path(audit_dir),
                                  tenant=tenant, mailbox=mailbox,
                                  folder=folder, log_id=log_id)
    checked = cycle.review(candidate=Path(candidate), consent_local_read=True, **scope)
    audit("review", checked)
    if checked.get("csv_sha256") != expected_review_csv_sha256:
        raise ReceiptError("Candidate changed since explicit review")
    # Refuse a stale predecessor BEFORE the first append. The checkpoint
    # commit still performs its authoritative exclusive-create CAS after
    # authenticated membership verification; this is only an early guard.
    predecessor = inspect_checkpoint(
        **scope, ledger_dir=Path(ledger_dir), consent_local_read=True)
    if predecessor is None:
        if expected_previous_candidate_sha256 is not None:
            raise ReceiptError("Missing predecessor checkpoint; append refused")
    elif (type(expected_previous_candidate_sha256) is not str
          or predecessor["candidate_sha256"] != expected_previous_candidate_sha256
          or predecessor["log_id"] != log_id):
        raise ReceiptError("Stale or different predecessor checkpoint; append refused")
    shared = dict(**scope, log_id=log_id, confirm_log_id=confirm_log_id,
                  candidate=Path(candidate), csv_path=Path(csv_path),
                  receipt_file=Path(receipt_file), port=port, session=session)
    # No mutation until all operator approvals and digest checks have passed.
    appended = cycle.append(**shared, consent_local_read=True,
                            consent_local_append=True,
                            confirm_candidate_reviewed=True,
                            expected_review_csv_sha256=expected_review_csv_sha256)
    audit("append", appended)
    if appended["status"] != "receipt_persisted":
        raise UncertainReceipt("Append outcome uncertain")
    handoff = cycle.handoff(**shared, journal_dir=Path(journal_dir),
                            handoff_dir=Path(handoff_dir),
                            successor_label=successor_label,
                            consent_local_read=True,
                            confirm_removals_reviewed=True)
    if handoff["status"] != "next_round_handoff_prepared":
        raise UncertainReceipt("Handoff outcome uncertain")
    # Persist an independently verifiable handoff receipt before checkpoint
    # advancement. Audit failure after append requires manual reconciliation.
    audit("handoff", handoff)
    committed = cycle.checkpoint_commit(
        **shared, journal_dir=Path(journal_dir), handoff_dir=Path(handoff_dir),
        ledger_dir=Path(ledger_dir), successor_label=successor_label,
        expected_previous_candidate_sha256=expected_previous_candidate_sha256,
        consent_local_read=True, confirm_removals_reviewed=True,
        confirm_checkpoint_commit=True)
    audit("checkpoint-commit", committed)
    return {"format": FORMAT, "status": "checkpoint_committed",
            "generation": committed["generation"],
            "idempotent_replay": committed.get("idempotent_replay", False),
            "automatic_retry_allowed": False, "provider_requests": 0}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preflight-only", action="store_true",
                   help="Read-only candidate/checkpoint preflight; never append or commit")
    for name in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id",
                 "expected-review-csv-sha256", "successor-label"):
        p.add_argument("--" + name)
    for name in ("grant-file", "candidate", "ledger-dir", "csv-path",
                 "receipt-file", "journal-dir", "handoff-dir"):
        p.add_argument("--" + name, type=Path)
    p.add_argument("--expected-previous-candidate-sha256")
    p.add_argument("--port", type=int, default=8765)
    for name in ("consent-local-read", "consent-local-append",
                 "confirm-candidate-reviewed", "confirm-removals-reviewed",
                 "confirm-checkpoint-commit"):
        p.add_argument("--" + name, action="store_true")
    args = vars(p.parse_args(argv))
    preflight_only = args.pop("preflight_only")
    required = ("tenant", "mailbox", "folder", "log_id", "confirm_log_id",
                "grant_file", "candidate", "ledger_dir", "expected_review_csv_sha256")
    if not preflight_only:
        required += ("successor_label", "csv_path", "receipt_file",
                     "journal_dir", "handoff_dir")
    missing = [key.replace("_", "-") for key in required if args[key] is None]
    if missing:
        p.error("required arguments: " + ", ".join("--" + key for key in missing))
    args["session"] = os.environ.get("OWG_DASHBOARD_SESSION")
    try:
        if preflight_only:
            result = preflight(**{key: args[key] for key in (
                "tenant", "mailbox", "folder", "log_id", "confirm_log_id",
                "grant_file", "candidate", "ledger_dir", "expected_review_csv_sha256",
                "expected_previous_candidate_sha256", "consent_local_read")})
        else:
            result = execute(**args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReceiptError, DeltaError, UncertainReceipt, AuditWriteError, OSError, ValueError,
            TypeError, KeyError):
        # A failed post-append operation may already have written events.
        # Never suggest retrying this composite transaction.
        print(json.dumps({"format": FORMAT,
                          "status": ("preflight_rejected" if preflight_only
                                     else "manual_reconciliation_required"),
                          "automatic_retry_allowed": False}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
