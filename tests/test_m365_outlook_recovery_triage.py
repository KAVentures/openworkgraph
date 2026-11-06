"""Functional recovery decisions without provider calls or mutations."""
from pathlib import Path
from unittest.mock import patch

import pytest

from server import m365_outlook_recovery_triage as module


@pytest.fixture
def paths(tmp_path):
    return dict(tenant="tenant", mailbox="mailbox", folder="inbox",
                grant_file=tmp_path / "grant", candidate=tmp_path / "candidate",
                csv_path=tmp_path / "data.csv", receipt_file=tmp_path / "receipt",
                log_id="log1", confirm_log_id="log1", port=8765, session="session",
                consent_local_read=True, confirm_removals_reviewed=True)


def test_missing_consent_refuses_before_read(paths):
    paths["consent_local_read"] = False
    with pytest.raises(ValueError):
        module.triage(**paths)


def test_verified_receipt_is_read_only_and_requires_separate_handoff(paths):
    with patch.object(module, "_grant"), patch.object(
        module, "authorize_resume", return_value={
            "candidate_sha256": "a" * 64, "log_id": "log1",
            "events_verified_by_count": 2}) as gate:
        out = module.triage(**paths)
    assert out["status"] == "receipt_verified"
    assert out["automatic_retry_allowed"] is False
    assert out["checkpoint_advanced"] is False
    assert out["owg_writes"] == 0
    assert gate.call_count == 1


def test_uncertain_receipt_requires_manual_reconciliation(paths):
    with patch.object(module, "_grant"), patch.object(
        module, "authorize_resume", side_effect=ValueError("possibly committed")):
        out = module.triage(**paths)
    assert out["status"] == "manual_reconciliation_required"
    assert out["automatic_retry_allowed"] is False


def test_partial_handoff_is_not_treated_as_success(paths, tmp_path):
    journal, handoff = tmp_path / "journal", tmp_path / "handoff"
    journal.mkdir(mode=0o700)
    handoff.mkdir(mode=0o700)
    paths.update(journal_dir=journal, handoff_dir=handoff)
    (journal / ("resume-" + "0" * 64 + ".json")).write_text("{}")
    with patch.object(module, "_grant"), patch.object(
        module, "authorize_resume", return_value={
            "candidate_sha256": "a" * 64, "log_id": "log1",
            "events_verified_by_count": 2}):
        # An unrelated artifact does not certify the current candidate.
        assert module.triage(**paths)["status"] == "receipt_verified"


def test_one_handoff_directory_refuses(paths, tmp_path):
    paths["journal_dir"] = tmp_path
    with pytest.raises(ValueError):
        module.triage(**paths)
