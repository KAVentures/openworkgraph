"""Race-safety regression for the real M365 reviewed transaction entrypoint.

A checkpoint that changes while a candidate is reviewed must not result in
an authenticated OWG append. No live Microsoft tenant is used.
"""
import os
from unittest.mock import patch

import pytest

from server import m365_outlook_reviewed_transaction as transaction
from server.m365_outlook_owg_receipt import ReceiptError


@pytest.mark.parametrize("before,after", [
    (None, {"generation": 1, "candidate_sha256": "a" * 64, "log_id": "log"}),
    ({"generation": 1, "candidate_sha256": "a" * 64, "log_id": "log"},
     {"generation": 2, "candidate_sha256": "b" * 64, "log_id": "log"}),
])
def test_checkpoint_race_during_review_refuses_append(monkeypatch, before, after):
    if os.name == "posix":
        monkeypatch.setenv("OWG_M365_AUDIT_DIR", "/private/audit")
    else:
        monkeypatch.delenv("OWG_M365_AUDIT_DIR", raising=False)
    digest = "c" * 64
    reviewed = {
        "status": "review_ready", "csv_sha256": digest,
        "event_snapshots": 1, "removal_markers_for_review": 0, "pages": 1,
    }
    kwargs = dict(
        tenant="tenant", mailbox="mailbox", folder="folder",
        log_id="log", confirm_log_id="log",
        grant_file="/private/grant", candidate="/private/candidate",
        csv_path="/private/events.csv", receipt_file="/private/receipt",
        journal_dir="/private/journal", handoff_dir="/private/handoff",
        ledger_dir="/private/ledger", successor_label="next",
        expected_review_csv_sha256=digest,
        expected_previous_candidate_sha256=(
            None if before is None else before["candidate_sha256"]),
        consent_local_read=True, consent_local_append=True,
        confirm_candidate_reviewed=True, confirm_removals_reviewed=True,
        confirm_checkpoint_commit=True,
    )
    with (
        patch.object(transaction, "inspect_checkpoint", side_effect=[before, after]) as inspect,
        patch.object(transaction.cycle, "review", return_value=reviewed) as review,
        patch.object(transaction, "record_operator_phase") as audit,
        patch.object(transaction, "preflight_operator_audit") as audit_preflight,
        patch.object(transaction.cycle, "append") as append,
        patch.object(transaction.cycle, "handoff") as handoff,
        patch.object(transaction.cycle, "checkpoint_commit") as commit,
        pytest.raises(ReceiptError, match="Checkpoint changed during candidate review"),
    ):
        transaction.execute(**kwargs)
    if os.name == "posix":
        audit_preflight.assert_called_once()
    audit.assert_not_called()
    assert inspect.call_count == 2
    review.assert_called_once()
    append.assert_not_called()
    handoff.assert_not_called()
    commit.assert_not_called()
