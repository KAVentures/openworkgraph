"""Read-only attempt inspection against the production inventory delegate."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server.m365_outlook_attempt_inspection import inspect_attempt, FORMAT
from server.m365_outlook_verified_append_receipt import FORMAT as ATTEMPT_FORMAT
from server.m365_outlook_owg_receipt import ReceiptError


def _setup(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    receipt = private / "receipt-test.json"
    latch = private / "receipt-test.json.attempt"
    latch.write_text(json.dumps({
        "format": ATTEMPT_FORMAT, "status": "attempt_started",
        "log_id": "target123", "csv_sha256": "a" * 64,
        "checkpoint_advanced": False, "automatic_retry": False,
    }))
    latch.chmod(0o600)
    return receipt


def _call(receipt, **kwargs):
    options = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                   grant_file=receipt.parent / "grant.json",
                   receipt_file=receipt, log_id="target123",
                   confirm_log_id="target123", port=8765,
                   session="session", consent_local_read=True,
                   opener=object())
    options.update(kwargs)
    return inspect_attempt(**options)


def test_live_inventory_read_is_indeterminate_and_never_writes(tmp_path):
    receipt = _setup(tmp_path)
    with patch("server.m365_outlook_attempt_inspection._grant") as grant, \
         patch("server.m365_outlook_attempt_inspection._scope", return_value="a" * 64), \
         patch("server.m365_outlook_attempt_inspection._session", return_value="session"), \
         patch("server.m365_outlook_attempt_inspection._inventory", return_value=[
             {"log_id": "target123", "source_format": "csv", "events": 12,
              "source_identity": {"namespaces": [{
                  "source_namespace": "m365_" + "a" * 32,
                  "case_namespace": "m365_" + "a" * 32}]}}
         ]) as inventory:
        result = _call(receipt)
    assert result["format"] == FORMAT
    assert result["status"] == "indeterminate"
    assert result["inventory_events_now"] == 12
    assert result["event_membership_verified"] is False
    assert result["safe_to_retry"] is False
    assert result["owg_write_requests"] == 0
    assert result["provider_requests"] == 0
    assert grant.call_count == 2
    inventory.assert_called_once()
    assert receipt.with_name(receipt.name + ".attempt").exists()
    assert not receipt.exists()


def test_missing_consent_refuses_before_inventory(tmp_path):
    receipt = _setup(tmp_path)
    with patch("server.m365_outlook_attempt_inspection._inventory") as inventory:
        with pytest.raises(ReceiptError):
            _call(receipt, consent_local_read=False)
        inventory.assert_not_called()


def test_existing_receipt_refuses(tmp_path):
    receipt = _setup(tmp_path)
    receipt.write_text("{}")
    with pytest.raises(ReceiptError):
        _call(receipt)


@pytest.mark.parametrize("change", [
    {"log_id": "other"}, {"status": "verified"},
    {"automatic_retry": True}, {"csv_sha256": "bad"},
])
def test_tampered_latch_refuses_without_inventory(tmp_path, change):
    receipt = _setup(tmp_path)
    latch = receipt.with_name(receipt.name + ".attempt")
    data = json.loads(latch.read_text())
    data.update(change)
    latch.write_text(json.dumps(data))
    with patch("server.m365_outlook_attempt_inspection._inventory") as inventory:
        with pytest.raises(ReceiptError):
            _call(receipt)
        inventory.assert_not_called()


def test_grant_revocation_refuses_before_inventory(tmp_path):
    receipt = _setup(tmp_path)
    with patch("server.m365_outlook_attempt_inspection._scope", return_value="a" * 64), \
         patch("server.m365_outlook_attempt_inspection._grant", side_effect=ReceiptError("revoked")), \
         patch("server.m365_outlook_attempt_inspection._inventory") as inventory:
        with pytest.raises(ReceiptError):
            _call(receipt)
        inventory.assert_not_called()
