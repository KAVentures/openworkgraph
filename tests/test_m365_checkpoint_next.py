"""Checkpoint-anchored extraction refuses uncommitted state before Graph GET."""
from pathlib import Path
from unittest.mock import patch

import pytest

from server import m365_outlook_checkpoint_next as next_round
from server.m365_outlook_owg_receipt import ReceiptError

SHA = "a" * 64
CURSOR = "https://graph.microsoft.com/v1.0/me/mailFolders/f/messages/delta?%24deltatoken=abc"


def args():
    return dict(tenant="t", mailbox="m", folder="f", log_id="log",
                confirm_log_id="log", grant_file=Path("grant"),
                ledger_dir=Path("ledger"), previous_candidate=Path("private/old.json"),
                previous_csv=Path("private/old.csv"), receipt_file=Path("receipt"),
                journal_dir=Path("journal"), handoff_dir=Path("handoff"),
                successor_label="next", output=Path("private/new.csv"),
                candidate=Path("private/new.json"), token="token",
                consent_local_read=True, consent_delta_read=True,
                consent_local_export=True, confirm_removals_reviewed=True)


def test_committed_predecessor_delegates_strict_membership():
    entry = dict(log_id="log", cursor_url=CURSOR, candidate_sha256=SHA, generation=3)
    with (patch.object(next_round, "inspect_checkpoint", return_value=entry),
          patch.object(next_round, "_candidate", return_value=CURSOR),
          patch.object(next_round, "_private_read", return_value=b'{"csv_sha256":"' + SHA.encode() + b'","csv_name":"old.csv"}'),
          patch.object(next_round, "run_verified_next_round",
                       return_value={"status": "next_round_review_ready",
                                     "event_snapshots": 2, "removal_markers_for_review": 1}) as run):
        result = next_round.extract_from_checkpoint(**args())
    assert result["status"] == "review_required"
    assert result["committed_predecessor_generation"] == 3
    assert result["checkpoint_advanced"] is False
    assert run.call_args.kwargs["require_event_membership"] is True


@pytest.mark.parametrize("change", [
    {"consent_delta_read": False}, {"confirm_removals_reviewed": False},
    {"confirm_log_id": "other"},
])
def test_no_read_without_explicit_consent(change):
    values = args()
    values.update(change)
    with patch.object(next_round, "run_verified_next_round") as run:
        with pytest.raises(ReceiptError):
            next_round.extract_from_checkpoint(**values)
        run.assert_not_called()


@pytest.mark.parametrize("entry", [
    None,
    {"log_id": "other", "cursor_url": CURSOR, "candidate_sha256": SHA, "generation": 1},
    {"log_id": "log", "cursor_url": "wrong", "candidate_sha256": SHA, "generation": 1},
    {"log_id": "log", "cursor_url": CURSOR, "candidate_sha256": "b"*64, "generation": 1},
])
def test_missing_or_stale_checkpoint_never_calls_graph(entry):
    with (patch.object(next_round, "inspect_checkpoint", return_value=entry),
          patch.object(next_round, "_candidate", return_value=CURSOR),
          patch.object(next_round, "_private_read", return_value=b'{"csv_sha256":"' + SHA.encode() + b'","csv_name":"old.csv"}'),
          patch.object(next_round, "run_verified_next_round") as run):
        with pytest.raises(ReceiptError):
            next_round.extract_from_checkpoint(**args())
        run.assert_not_called()
