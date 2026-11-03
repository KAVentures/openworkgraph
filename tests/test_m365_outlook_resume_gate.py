"""Integration boundary tests for explicit Outlook delta resume authorization."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server.m365_outlook_resume_gate import authorize_resume
from server.m365_outlook_owg_receipt import ReceiptError, FORMAT as RECEIPT_FORMAT


def _run(tmp_path, *, receipt_changes=None, current=5, consent=True, reviewed=True):
    candidate = tmp_path / "candidate.json"
    csv_path = tmp_path / "events.csv"
    receipt_path = tmp_path / "receipt.json"
    receipt = {
        "format": RECEIPT_FORMAT, "status": "verified", "log_id": "log123",
        "csv_sha256": "a" * 64, "checkpoint_advanced": False,
        "provider_requests": 0, "events_before": 3, "events_after": 5,
        "added_events": 2, "duplicate_events_skipped": 0, "batch": 1,
    }
    receipt.update(receipt_changes or {})
    receipt_path.write_text(json.dumps(receipt))
    scope = "b" * 64
    candidate_data = {"csv_sha256": "a" * 64, "events": 2}
    with (patch("server.m365_outlook_resume_gate._scope", return_value=scope),
          patch("server.m365_outlook_resume_gate._grant"),
          patch("server.m365_outlook_resume_gate._artifact", return_value=(b"", candidate_data)),
          patch("server.m365_outlook_resume_gate._delta_private_parent"),
          patch("server.m365_outlook_resume_gate._private_read", side_effect=lambda p, n: p.read_bytes()),
          patch("server.m365_outlook_resume_gate._session", return_value="session"),
          patch("server.m365_outlook_resume_gate._inventory", return_value=[{"log_id": "log123"}]),
          patch("server.m365_outlook_resume_gate._target", return_value=current)):
        return authorize_resume(
            tenant="tenant", mailbox="mailbox", folder="folder",
            grant_file=tmp_path / "grant", candidate=candidate,
            csv_path=csv_path, receipt_file=receipt_path,
            log_id="log123", confirm_log_id="log123",
            port=8765, session="session", consent_local_read=consent,
            confirm_removals_reviewed=reviewed, opener=object())


def test_valid_receipt_requires_operator_and_inventory(tmp_path):
    result = _run(tmp_path)
    assert result["status"] == "resume_authorized_for_operator"
    assert result["checkpoint_advanced"] is False
    assert result["event_level_proof"] is False


@pytest.mark.parametrize("changes", [
    {"csv_sha256": "c" * 64}, {"status": "unverified"},
    {"log_id": "another"}, {"checkpoint_advanced": True},
    {"added_events": 1}, {"events_after": 4}, {"batch": 0},
    {"provider_requests": 1},
])
def test_mismatched_receipts_refused(tmp_path, changes):
    with pytest.raises(ReceiptError):
        _run(tmp_path, receipt_changes=changes)


def test_stale_inventory_refused(tmp_path):
    with pytest.raises(ReceiptError):
        _run(tmp_path, current=6)


def test_explicit_consents_required(tmp_path):
    with pytest.raises(ReceiptError):
        _run(tmp_path, consent=False)
    with pytest.raises(ReceiptError):
        _run(tmp_path, reviewed=False)


def test_duplicate_receipt_json_keys_refused(tmp_path):
    # Duplicate keys must not silently override the authenticated receipt.
    with pytest.raises(ReceiptError):
        _run(tmp_path, receipt_changes={"events_before": "3"})
