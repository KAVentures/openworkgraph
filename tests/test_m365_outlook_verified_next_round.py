"""Verified next-round entrypoint: refusal and production handoff/export integration."""
import hashlib
import json
from pathlib import Path

import pytest

from server import m365_outlook_verified_next_round as mod
from server.m365_outlook_owg_receipt import ReceiptError
from server.m365_outlook_resume_journal import _canonical


def _args(tmp_path):
    for directory in ("prior", "journal", "handoff", "next"):
        (tmp_path / directory).mkdir(mode=0o700)
    prior = tmp_path / "prior" / "round1.json"
    prior.write_text('{"candidate":"example"}')
    prior.chmod(0o600)
    identity = hashlib.sha256(_canonical({
        "candidate_sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
        "log_id": "log_test",
    })).hexdigest()
    handoff = tmp_path / "handoff" / ("handoff-" + identity + ".json")
    handoff.write_text('{"handoff":"example"}')
    handoff.chmod(0o600)
    return dict(
        tenant="tenant", mailbox="mailbox", folder="folder", log_id="log_test",
        confirm_log_id="log_test", grant_file=tmp_path / "prior" / "grant.json",
        previous_candidate=prior, previous_csv=tmp_path / "prior" / "round1.csv",
        receipt_file=tmp_path / "prior" / "receipt.json",
        journal_dir=tmp_path / "journal", handoff_dir=tmp_path / "handoff",
        successor_label="round2", output=tmp_path / "next" / "round2.csv",
        candidate=tmp_path / "next" / "round2.json", token="fake-token",
        consent_local_read=True, confirm_removals_reviewed=True,
        consent_delta_read=True, consent_local_export=True,
    )


def test_existing_verified_handoff_invokes_production_delta(monkeypatch, tmp_path):
    args = _args(tmp_path)
    calls = []
    def gate(**kwargs):
        calls.append(("gate", kwargs))
        return {"idempotent_replay": True}
    def delta(**kwargs):
        calls.append(("delta", kwargs))
        return {"provider_get_requests": 2, "event_snapshots": 3,
                "removal_markers_for_review": 1}
    monkeypatch.setattr(mod, "prepare_handoff", gate)
    monkeypatch.setattr(mod, "export_delta", delta)
    result = mod.run_verified_next_round(**args)
    assert [name for name, _ in calls] == ["gate", "delta"]
    assert calls[1][1]["previous_candidate"] == args["previous_candidate"]
    assert calls[1][1]["confirm_previous_import"] is True
    assert calls[1][1]["consent_read"] is True
    assert result["checkpoint_advanced"] is False
    assert result["removal_markers_for_review"] == 1


def test_missing_handoff_refuses_before_graph(monkeypatch, tmp_path):
    args = _args(tmp_path)
    for path in args["handoff_dir"].iterdir():
        path.unlink()
    monkeypatch.setattr(mod, "export_delta",
                        lambda **kwargs: pytest.fail("provider called"))
    with pytest.raises(ValueError):
        mod.run_verified_next_round(**args)


@pytest.mark.parametrize("flag", [
    "consent_local_read", "confirm_removals_reviewed",
    "consent_delta_read", "consent_local_export",
])
def test_each_consent_required_before_graph(monkeypatch, tmp_path, flag):
    args = _args(tmp_path)
    args[flag] = False
    monkeypatch.setattr(mod, "export_delta",
                        lambda **kwargs: pytest.fail("provider called"))
    with pytest.raises(ReceiptError):
        mod.run_verified_next_round(**args)


def test_changed_handoff_or_live_gate_refuses_before_graph(monkeypatch, tmp_path):
    args = _args(tmp_path)
    monkeypatch.setattr(mod, "prepare_handoff",
                        lambda **kwargs: (_ for _ in ()).throw(ReceiptError("revoked")))
    monkeypatch.setattr(mod, "export_delta",
                        lambda **kwargs: pytest.fail("provider called"))
    with pytest.raises(ReceiptError):
        mod.run_verified_next_round(**args)


def test_nonreplay_handoff_refuses_before_graph(monkeypatch, tmp_path):
    args = _args(tmp_path)
    monkeypatch.setattr(mod, "prepare_handoff",
                        lambda **kwargs: {"idempotent_replay": False})
    monkeypatch.setattr(mod, "export_delta",
                        lambda **kwargs: pytest.fail("provider called"))
    with pytest.raises(ReceiptError):
        mod.run_verified_next_round(**args)


def test_new_output_must_differ_from_prior(monkeypatch, tmp_path):
    args = _args(tmp_path)
    args["output"] = args["previous_csv"]
    monkeypatch.setattr(mod, "export_delta",
                        lambda **kwargs: pytest.fail("provider called"))
    with pytest.raises(ReceiptError):
        mod.run_verified_next_round(**args)
