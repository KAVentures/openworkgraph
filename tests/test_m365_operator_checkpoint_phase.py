"""Operator-facing checkpoint CLI contract and delegation."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server import m365_outlook_operator_cycle as cycle
from server.m365_outlook_owg_receipt import ReceiptError


def test_checkpoint_inspect_hides_cursor():
    with patch.object(cycle, "inspect_checkpoint", return_value={
            "generation": 3, "cursor_url": "secret-provider-cursor"}) as inspect:
        result = cycle.checkpoint_inspect(
            tenant="t", mailbox="m", folder="f", grant_file=Path("grant"),
            ledger_dir=Path("ledger"), consent_local_read=True)
    assert result["generation"] == 3
    assert "cursor_url" not in result
    assert result["provider_requests"] == 0
    assert inspect.call_args.kwargs["consent_local_read"] is True


def test_checkpoint_commit_requires_delegate_success():
    with patch.object(cycle, "commit_checkpoint", return_value={
            "status": "checkpoint_committed", "generation": 2,
            "idempotent_replay": False}) as commit:
        result = cycle.checkpoint_commit(
            consent_local_read=True, confirm_removals_reviewed=True,
            confirm_checkpoint_commit=True, tenant="t")
    assert result["generation"] == 2
    assert result["automatic_retry_allowed"] is False
    assert commit.call_args.kwargs["confirm_checkpoint_commit"] is True
    with patch.object(cycle, "commit_checkpoint", return_value={"status": "refused"}):
        with pytest.raises(ReceiptError):
            cycle.checkpoint_commit()


def test_cli_inspect_and_refusal(capsys):
    args = ["checkpoint-inspect", "--tenant", "t", "--mailbox", "m",
            "--folder", "f", "--grant-file", "grant", "--ledger-dir", "ledger",
            "--consent-local-read"]
    with patch.object(cycle, "inspect_checkpoint", return_value=None):
        assert cycle.main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "empty"
    with patch.object(cycle, "inspect_checkpoint", side_effect=ReceiptError("bad")):
        assert cycle.main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_cli_commit_routes_approval_and_predecessor(capsys):
    args = ["checkpoint-commit", "--tenant", "t", "--mailbox", "m",
            "--folder", "f", "--grant-file", "grant", "--ledger-dir", "ledger",
            "--log-id", "L", "--confirm-log-id", "L",
            "--candidate", "candidate", "--csv-path", "events.csv",
            "--receipt-file", "receipt", "--journal-dir", "journal",
            "--handoff-dir", "handoff", "--successor-label", "next",
            "--expected-previous-candidate-sha256", "a" * 64,
            "--consent-local-read", "--confirm-removals-reviewed",
            "--confirm-checkpoint-commit"]
    with patch.object(cycle, "commit_checkpoint", return_value={
            "status": "checkpoint_committed", "generation": 2,
            "idempotent_replay": False}) as commit:
        assert cycle.main(args) == 0
    assert commit.call_args.kwargs["expected_previous_candidate_sha256"] == "a" * 64
    assert commit.call_args.kwargs["confirm_checkpoint_commit"] is True
    assert json.loads(capsys.readouterr().out)["status"] == "checkpoint_committed"
    with patch.object(cycle, "commit_checkpoint", side_effect=ReceiptError("stale")):
        assert cycle.main(args) == 2
    assert json.loads(capsys.readouterr().out)["retry_allowed"] is False
