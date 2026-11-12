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

FORMAT = "openworkgraph.m365-reviewed-transaction.v1"


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
    for name in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id",
                 "successor-label", "expected-review-csv-sha256"):
        p.add_argument("--" + name, required=True)
    for name in ("grant-file", "candidate", "csv-path", "receipt-file",
                 "journal-dir", "handoff-dir", "ledger-dir"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--expected-previous-candidate-sha256")
    p.add_argument("--port", type=int, default=8765)
    for name in ("consent-local-read", "consent-local-append",
                 "confirm-candidate-reviewed", "confirm-removals-reviewed",
                 "confirm-checkpoint-commit"):
        p.add_argument("--" + name, action="store_true")
    args = vars(p.parse_args(argv))
    args["session"] = os.environ.get("OWG_DASHBOARD_SESSION")
    try:
        result = execute(**{k.replace("-", "_"): v for k, v in args.items()})
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReceiptError, DeltaError, UncertainReceipt, AuditWriteError, OSError, ValueError,
            TypeError, KeyError):
        # A failed post-append operation may already have written events.
        # Never suggest retrying this composite transaction.
        print(json.dumps({"format": FORMAT, "status": "manual_reconciliation_required",
                          "automatic_retry_allowed": False}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
