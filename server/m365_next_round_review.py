"""Single operator entrypoint for a checkpoint-bound next Microsoft 365 review.

The prepare command performs a bounded authenticated Graph read, then validates
the resulting private candidate and committed predecessor. It never appends
events or advances the checkpoint. Commit remains a separate explicit action
through m365_outlook_reviewed_transaction, with fresh approvals.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import m365_outlook_operator_cycle as cycle
from . import m365_outlook_reviewed_transaction as transaction
from .m365_outlook_owg_receipt import ReceiptError
from .m365_outlook_delta import DeltaError
from .m365_outlook_checkpoint import inspect_checkpoint
from .m365_operator_audit_writer import preflight_operator_audit, record_operator_phase, AuditWriteError

FORMAT = "openworkgraph.m365-next-round-review.v1"


def prepare(*, tenant, mailbox, folder, log_id, confirm_log_id, grant_file,
            previous_candidate, previous_csv, receipt_file, journal_dir,
            handoff_dir, ledger_dir, successor_label, output, candidate,
            expected_generation, expected_previous_candidate_sha256,
            token, port=8765, session=None, consent_local_read=False,
            consent_delta_read=False, consent_local_export=False,
            confirm_previous_removals_reviewed=False,
            require_event_membership=True, opener=None):
    """Produce a reviewable candidate, never an implicit authorization to commit."""
    if not all((consent_local_read is True, consent_delta_read is True,
                consent_local_export is True,
                confirm_previous_removals_reviewed is True,
                require_event_membership is True)):
        raise ReceiptError("Independent consent and verified predecessor membership required")
    if not log_id or log_id != confirm_log_id:
        raise ReceiptError("Destination log confirmation mismatch")
    if type(expected_generation) is not int or expected_generation < 1:
        raise ReceiptError("Exact committed generation required")
    if (type(expected_previous_candidate_sha256) is not str
            or len(expected_previous_candidate_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_previous_candidate_sha256)):
        raise ReceiptError("Exact predecessor digest required")
    audit_dir = os.environ.get("OWG_M365_AUDIT_DIR")
    if audit_dir:
        if os.name != "posix":
            raise ReceiptError("Audited extraction unsupported on this platform")
        preflight_operator_audit(Path(audit_dir))
    scope = dict(tenant=tenant, mailbox=mailbox, folder=folder,
                 grant_file=Path(grant_file))
    predecessor = inspect_checkpoint(
        **scope, ledger_dir=Path(ledger_dir), consent_local_read=True)
    if (predecessor is None or predecessor["generation"] != expected_generation
            or predecessor["candidate_sha256"] != expected_previous_candidate_sha256
            or predecessor["log_id"] != log_id):
        raise ReceiptError("Stale predecessor checkpoint")
    # Production extraction verifies the full predecessor candidate, persisted
    # handoff, authenticated OWG membership and checkpoint generation again.
    extracted = cycle.extract(
        **scope, log_id=log_id, confirm_log_id=confirm_log_id,
        previous_candidate=Path(previous_candidate), previous_csv=Path(previous_csv),
        receipt_file=Path(receipt_file), journal_dir=Path(journal_dir),
        handoff_dir=Path(handoff_dir), ledger_dir=Path(ledger_dir),
        expected_generation=expected_generation, successor_label=successor_label,
        output=Path(output), candidate=Path(candidate), token=token,
        port=port, session=session, opener=opener,
        consent_local_read=True, consent_delta_read=True,
        consent_local_export=True, confirm_removals_reviewed=True,
        require_event_membership=True)
    # No append can occur in prepare. If review fails, leave artifacts
    # uncommitted for manual inspection; never automatically retry extraction.
    checked = cycle.review(**scope, candidate=Path(candidate),
                           consent_local_read=True)
    digest = checked["csv_sha256"]
    proposal = transaction.preflight(
        **scope, log_id=log_id, confirm_log_id=confirm_log_id,
        candidate=Path(candidate), ledger_dir=Path(ledger_dir),
        expected_review_csv_sha256=digest,
        expected_previous_candidate_sha256=expected_previous_candidate_sha256,
        consent_local_read=True)
    if proposal["proposed_generation"] != expected_generation + 1:
        raise ReceiptError("Concurrent checkpoint transition")
    # The preflight is read-only, not a lock. Refuse to present a review
    # as current if another operator committed while it was running.
    current = inspect_checkpoint(
        **scope, ledger_dir=Path(ledger_dir), consent_local_read=True)
    if current != predecessor:
        raise ReceiptError("Checkpoint changed during review preflight")
    if (checked["event_snapshots"] != extracted["event_snapshots"]
            or checked["removal_markers_for_review"] != extracted["removal_markers_for_review"]):
        raise ReceiptError("Extracted candidate review count mismatch")
    result = {"format": FORMAT, "status": "operator_review_required",
              "reviewed_csv_sha256": digest,
              "proposed_generation": proposal["proposed_generation"],
              "event_snapshots": checked["event_snapshots"],
              "removal_markers_for_review": checked["removal_markers_for_review"],
              "checkpoint_advanced": False, "owg_append_performed": False,
              "automatic_retry_allowed": False,
              "next_step": "Inspect private candidate and removals; separately invoke reviewed transaction"}
    if audit_dir:
        record_operator_phase("extract", result, directory=Path(audit_dir),
                              tenant=tenant, mailbox=mailbox, folder=folder, log_id=log_id)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id",
                "successor-label", "expected-previous-candidate-sha256"):
        p.add_argument("--" + key, required=True)
    for key in ("grant-file", "previous-candidate", "previous-csv", "receipt-file",
                "journal-dir", "handoff-dir", "ledger-dir", "output", "candidate"):
        p.add_argument("--" + key, required=True, type=Path)
    p.add_argument("--expected-generation", required=True, type=int)
    p.add_argument("--port", type=int, default=8765)
    for key in ("consent-local-read", "consent-delta-read", "consent-local-export",
                "confirm-previous-removals-reviewed", "require-event-membership"):
        p.add_argument("--" + key, action="store_true")
    args = vars(p.parse_args(argv))
    args["token"] = os.environ.get("OWG_M365_GRAPH_TOKEN", "")
    args["session"] = os.environ.get("OWG_DASHBOARD_SESSION")
    try:
        print(json.dumps(prepare(**args), sort_keys=True))
        return 0
    except (ReceiptError, DeltaError, AuditWriteError, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"format": FORMAT, "status": "manual_review_required",
                          "automatic_retry_allowed": False,
                          "checkpoint_advanced": False}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
