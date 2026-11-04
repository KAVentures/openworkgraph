"""Contract tests for the consent-gated M365 event review preparation."""
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from server import m365_outlook_attempt_event_review as review
from server.m365_outlook_owg_receipt import ReceiptError


def _setup(monkeypatch, tmp_path):
    candidate = tmp_path / "candidate.json"
    csv_path = tmp_path / "events.csv"
    receipt = tmp_path / "receipt-1.json"
    output = tmp_path / "review-1.json"
    grant = tmp_path / "grant.json"
    body = b"case_id,activity,timestamp,source_event_id\n" + (
        b"outlookcase:one,Created,2026-11-01T00:00:00Z,outlookevent:one\n")
    csv_path.write_bytes(body)
    candidate.write_bytes(b"candidate")
    candidate.chmod(0o600)  # Production candidate artifacts are owner-private.
    scope = "a" * 64
    attempt = {"format": review.ATTEMPT_FORMAT, "status": "attempt_started",
               "log_id": "log_" + "a" * 16,
               "csv_sha256": hashlib.sha256(body).hexdigest(),
               "automatic_retry": False, "checkpoint_advanced": False}
    latch = tmp_path / "receipt-1.json.attempt"
    latch.write_text(json.dumps(attempt))
    latch.chmod(0o600)  # Production creates owner-only latches; never weaken the reader.
    monkeypatch.setattr(review, "_delta_private_parent", lambda p: None)
    monkeypatch.setattr(review, "_scope", lambda *a: scope)
    monkeypatch.setattr(review, "_grant", lambda *a: None)
    monkeypatch.setattr(review, "_artifact", lambda *a: (body, {
        "events": 1, "csv_sha256": hashlib.sha256(body).hexdigest()}))
    monkeypatch.setattr(review, "_session", lambda s: "session")
    monkeypatch.setattr(review, "_inventory", lambda *a: [{
        "log_id": attempt["log_id"], "events": 1}])
    monkeypatch.setattr(review, "_target", lambda *a: 1)
    monkeypatch.setattr(review, "_opener", lambda: object())
    return dict(tenant="tenant", mailbox="mailbox", folder="folder",
                grant_file=grant, candidate=candidate, csv_path=csv_path,
                receipt_file=receipt, output=output, log_id=attempt["log_id"],
                confirm_log_id=attempt["log_id"], session="session",
                consent_local_read=True, consent_private_export=True)


def test_private_review_is_not_a_reconciliation(monkeypatch, tmp_path):
    kw = _setup(monkeypatch, tmp_path)
    result = review.prepare_event_review(**kw)
    stored = json.loads(kw["output"].read_text())
    assert result["status"] == "review_manifest_written"
    assert result["safe_to_retry"] is False
    assert stored["event_ids"] == ["outlookevent:one"]
    assert stored["event_membership_verified"] is False
    assert stored["owg_write_requests"] == 0
    assert "event_ids" not in result


@pytest.mark.parametrize("change", [
    {"consent_local_read": False},
    {"consent_private_export": False},
    {"confirm_log_id": "log_" + "b" * 16},
])
def test_refuses_without_exact_authority(monkeypatch, tmp_path, change):
    kw = _setup(monkeypatch, tmp_path)
    kw.update(change)
    with pytest.raises(ReceiptError):
        review.prepare_event_review(**kw)
    assert not kw["output"].exists()


def test_refuses_tampered_attempt(monkeypatch, tmp_path):
    kw = _setup(monkeypatch, tmp_path)
    latch = tmp_path / "receipt-1.json.attempt"
    data = json.loads(latch.read_text())
    data["csv_sha256"] = "0" * 64
    latch.write_text(json.dumps(data))
    with pytest.raises(ReceiptError):
        review.prepare_event_review(**kw)
    assert not kw["output"].exists()


def test_refuses_existing_review_without_overwrite(monkeypatch, tmp_path):
    kw = _setup(monkeypatch, tmp_path)
    kw["output"].write_text("existing")
    with pytest.raises(ReceiptError):
        review.prepare_event_review(**kw)
    assert kw["output"].read_text() == "existing"


def test_revocation_before_private_write_refuses(monkeypatch, tmp_path):
    kw = _setup(monkeypatch, tmp_path)
    calls = 0
    def grant(*args):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise ReceiptError("revoked")
    monkeypatch.setattr(review, "_grant", grant)
    with pytest.raises(ReceiptError):
        review.prepare_event_review(**kw)
    assert not kw["output"].exists()
