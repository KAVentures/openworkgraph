"""Operator-facing membership dispatch and fail-closed contract."""
import json
from unittest.mock import patch

from server import m365_outlook_operator_cycle as cycle


def _args():
    return ["membership", "--tenant", "t", "--mailbox", "m", "--folder", "f",
            "--grant-file", "grant.json", "--candidate", "candidate.json",
            "--csv-path", "candidate.csv", "--log-id", "log_0123456789abcdef",
            "--confirm-log-id", "log_0123456789abcdef", "--consent-local-read"]


def test_membership_cli_complete(monkeypatch, capsys):
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "test-session")
    def fake(**kwargs):
        assert kwargs["consent_local_read"] is True
        assert kwargs["confirm_log_id"] == kwargs["log_id"]
        assert kwargs["session"] == "test-session"
        return {"status": "all_candidate_events_present", "matched_events": 2}
    with patch.object(cycle, "reconcile_membership", side_effect=fake):
        assert cycle.main(_args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["phase"] == "membership"
    assert result["automatic_retry_allowed"] is False
    assert result["checkpoint_advanced"] is False


def test_membership_cli_missing_requires_manual_review(capsys):
    with patch.object(cycle, "reconcile_membership", return_value={
        "status": "manual_reconciliation_required", "missing_events": 1}):
        assert cycle.main(_args()) == 3
    assert json.loads(capsys.readouterr().out)["automatic_retry_allowed"] is False


def test_membership_cli_refuses_without_consent(capsys):
    with patch.object(cycle, "reconcile_membership") as backend:
        assert cycle.main([x for x in _args() if x != "--consent-local-read"]) == 2
        backend.assert_not_called()
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_membership_cli_refuses_invalid_backend_status(capsys):
    with patch.object(cycle, "reconcile_membership", return_value={"status": "uncertain"}):
        assert cycle.main(_args()) == 2
    assert json.loads(capsys.readouterr().out)["retry_allowed"] is False
