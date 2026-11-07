"""User-facing operator-cycle gate integration; no network or filesystem writes."""
from unittest.mock import patch

import pytest

from server import m365_outlook_operator_cycle as cycle
from server.m365_outlook_owg_receipt import ReceiptError


CONSENT = dict(consent_local_read=True, consent_delta_read=True,
               consent_local_export=True, confirm_removals_reviewed=True)


def test_extract_opt_in_forwards_gate_before_provider_request():
    with patch.object(cycle, "run_verified_next_round", return_value={
        "status": "next_round_review_ready", "event_snapshots": 2,
        "removal_markers_for_review": 0}) as run:
        result = cycle.extract(require_event_membership=True, **CONSENT)
    assert result["phase"] == "extract"
    assert result["checkpoint_advanced"] is False
    assert result["owg_append_performed"] is False
    assert run.call_args.kwargs["require_event_membership"] is True


def test_extract_default_preserves_legacy_behavior():
    with patch.object(cycle, "run_verified_next_round", return_value={
        "status": "next_round_review_ready", "event_snapshots": 0,
        "removal_markers_for_review": 0}) as run:
        cycle.extract(**CONSENT)
    assert run.call_args.kwargs["require_event_membership"] is False


def test_extract_refusal_never_falls_back_to_ungated_request():
    with patch.object(cycle, "run_verified_next_round",
                      side_effect=ReceiptError("membership incomplete")) as run:
        with pytest.raises(ReceiptError):
            cycle.extract(require_event_membership=True, **CONSENT)
    assert run.call_count == 1
    assert run.call_args.kwargs["require_event_membership"] is True


def test_extract_without_consent_does_not_invoke_delegate():
    with patch.object(cycle, "run_verified_next_round") as run:
        with pytest.raises(ReceiptError):
            cycle.extract(require_event_membership=True)
    run.assert_not_called()


def test_operator_cli_accepts_membership_switch_and_refuses_without_consent(capsys):
    rc = cycle.main([
        "extract", "--tenant", "t", "--mailbox", "m", "--folder", "f",
        "--log-id", "log_0123456789abcdef",
        "--confirm-log-id", "log_0123456789abcdef",
        "--grant-file", "grant.json", "--previous-candidate", "old.json",
        "--previous-csv", "old.csv", "--receipt-file", "receipt.json",
        "--journal-dir", "journal", "--handoff-dir", "handoff",
        "--output", "new.csv", "--candidate", "new.json",
        "--successor-label", "next", "--require-event-membership",
    ])
    assert rc == 2
    assert '"status": "refused"' in capsys.readouterr().out
