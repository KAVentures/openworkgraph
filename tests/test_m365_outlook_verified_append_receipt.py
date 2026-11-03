"""Durable operator append: real wrapper, private files, refusal and no replay."""
import json
from pathlib import Path

import pytest

from server import m365_outlook_verified_append_receipt as mod
from server.m365_outlook_owg_receipt import ReceiptError, UncertainReceipt


def _args(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    return dict(
        tenant="tenant", mailbox="mailbox", folder="folder",
        grant_file=root / "grant.json", candidate=root / "round.json",
        csv_path=root / "round.csv", receipt_file=root / "receipt-round.json",
        log_id="log_test", confirm_log_id="log_test", port=8765,
        session="test-session", consent_local_read=True,
        consent_local_append=True,
    )


def _stub_validation(monkeypatch):
    monkeypatch.setattr(mod, "_grant", lambda *args: None)
    monkeypatch.setattr(mod, "_scope", lambda *args: "scope")
    monkeypatch.setattr(mod, "_artifact", lambda *args: (
        b"case_id,activity,timestamp,source_event_id\n",
        {"csv_sha256": "a" * 64},
    ))


def _verified(**kwargs):
    return dict(
        format="openworkgraph.m365-outlook-append-receipt.v1",
        status="verified", log_id=kwargs["log_id"], csv_sha256="a" * 64,
        events_before=0, events_after=2, added_events=2,
        duplicate_events_skipped=0, batch=1,
        checkpoint_advanced=False, provider_requests=0,
    )


def test_persists_resume_compatible_receipt_and_refuses_replay(tmp_path, monkeypatch):
    args = _args(tmp_path)
    _stub_validation(monkeypatch)
    calls = []
    def append(**kwargs):
        calls.append(kwargs)
        return _verified(**kwargs)
    monkeypatch.setattr(mod, "append_verified", append)
    result = mod.append_and_record(**args)
    assert result["status"] == "receipt_persisted"
    assert len(calls) == 1
    assert calls[0]["consent_local_append"] is True
    assert calls[0]["consent_local_read"] is True
    persisted = json.loads(args["receipt_file"].read_text())
    assert persisted["status"] == "verified"
    assert persisted["events_after"] == 2
    assert args["receipt_file"].with_name("receipt-round.json.attempt").exists()
    with pytest.raises(ReceiptError):
        mod.append_and_record(**args)
    assert len(calls) == 1


def test_uncertain_post_latches_and_never_retries(tmp_path, monkeypatch):
    args = _args(tmp_path)
    _stub_validation(monkeypatch)
    calls = []
    def append(**kwargs):
        calls.append(1)
        raise UncertainReceipt("POST may have committed")
    monkeypatch.setattr(mod, "append_verified", append)
    with pytest.raises(UncertainReceipt):
        mod.append_and_record(**args)
    assert not args["receipt_file"].exists()
    assert args["receipt_file"].with_name("receipt-round.json.attempt").exists()
    with pytest.raises(ReceiptError):
        mod.append_and_record(**args)
    assert len(calls) == 1


@pytest.mark.parametrize("key", ["consent_local_read", "consent_local_append"])
def test_consent_refused_before_attempt(tmp_path, monkeypatch, key):
    args = _args(tmp_path)
    args[key] = False
    _stub_validation(monkeypatch)
    monkeypatch.setattr(mod, "append_verified",
                        lambda **kwargs: pytest.fail("append called"))
    with pytest.raises(ReceiptError):
        mod.append_and_record(**args)
    assert not args["receipt_file"].with_name("receipt-round.json.attempt").exists()


def test_existing_receipt_blocks_before_post(tmp_path, monkeypatch):
    args = _args(tmp_path)
    _stub_validation(monkeypatch)
    args["receipt_file"].write_text("{}")
    args["receipt_file"].chmod(0o600)
    monkeypatch.setattr(mod, "append_verified",
                        lambda **kwargs: pytest.fail("append called"))
    with pytest.raises(ReceiptError):
        mod.append_and_record(**args)


def test_revoked_grant_refuses_before_attempt(tmp_path, monkeypatch):
    args = _args(tmp_path)
    monkeypatch.setattr(mod, "_scope", lambda *a: "scope")
    monkeypatch.setattr(mod, "_grant",
                        lambda *a: (_ for _ in ()).throw(ReceiptError("revoked")))
    monkeypatch.setattr(mod, "append_verified",
                        lambda **kwargs: pytest.fail("append called"))
    with pytest.raises(ReceiptError):
        mod.append_and_record(**args)
    assert not args["receipt_file"].with_name("receipt-round.json.attempt").exists()


def test_zero_event_receipt_is_persisted(tmp_path, monkeypatch):
    args = _args(tmp_path)
    _stub_validation(monkeypatch)
    monkeypatch.setattr(mod, "append_verified", lambda **kwargs: dict(
        format="openworkgraph.m365-outlook-append-receipt.v1",
        status="verified_empty", log_id="log_test", csv_sha256="a" * 64,
        events_before=3, events_after=3,
        checkpoint_advanced=False, provider_requests=0,
    ))
    assert mod.append_and_record(**args)["events_after"] == 3
    assert json.loads(args["receipt_file"].read_text())["status"] == "verified_empty"
