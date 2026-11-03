"""Functional persistence and refusal tests for operator next-round handoff."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from server import m365_outlook_resume_handoff as handoff
from server.m365_outlook_resume_journal import FORMAT as JOURNAL_FORMAT, _canonical
from server.m365_outlook_owg_receipt import ReceiptError


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    journal = tmp_path / "journal"
    output = tmp_path / "handoff"
    journal.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    if os.name != "nt":
        journal.chmod(0o700)
        output.chmod(0o700)
    decision = {
        "candidate_sha256": "a" * 64,
        "log_id": "log_0123456789abcdef",
        "successor_label": "round-002",
        "events_verified_by_count": 3,
        "checkpoint_advanced": False,
        "provider_requests": 0,
    }
    calls = []

    def verified_resume(*, journal_dir, successor_label, **kwargs):
        calls.append(kwargs)
        if kwargs.get("revoked"):
            raise ReceiptError("Grant revoked")
        if kwargs.get("changed"):
            raise ReceiptError("Inventory changed")
        if successor_label != decision["successor_label"]:
            raise ReceiptError("Conflicting successor")
        identity = hashlib.sha256(_canonical({
            "candidate_sha256": decision["candidate_sha256"],
            "log_id": decision["log_id"],
        })).hexdigest()
        path = Path(journal_dir) / ("resume-" + identity + ".json")
        entry = {"format": JOURNAL_FORMAT, "status": "operator_resume_recorded",
                 **decision}
        if not path.exists():
            path.write_bytes(_canonical(entry) + b"\n")
            if os.name != "nt":
                path.chmod(0o600)
        return dict(decision)

    monkeypatch.setattr(handoff, "record_resume", verified_resume)
    return journal, output, calls


def test_persists_verified_handoff_and_exact_replay(dirs):
    journal, output, calls = dirs
    first = handoff.prepare_handoff(journal_dir=journal, handoff_dir=output,
                                    successor_label="round-002", consent=True)
    assert first["status"] == "next_round_handoff_prepared"
    assert first["idempotent_replay"] is False
    assert first["checkpoint_advanced"] is False
    assert first["provider_requests"] == 0
    assert first["operator_must_start_next_extraction"] is True
    assert "token" not in json.dumps(first).lower()
    assert len(list(output.iterdir())) == 1
    second = handoff.prepare_handoff(journal_dir=journal, handoff_dir=output,
                                     successor_label="round-002", consent=True)
    assert second["idempotent_replay"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("reason", ["revoked", "changed"])
def test_fresh_gate_refusal_does_not_create_handoff(dirs, reason):
    journal, output, _ = dirs
    with pytest.raises(ReceiptError):
        handoff.prepare_handoff(journal_dir=journal, handoff_dir=output,
                                successor_label="round-002", **{reason: True})
    assert not list(output.iterdir())


def test_conflicting_or_corrupted_handoff_fails_closed(dirs):
    journal, output, _ = dirs
    handoff.prepare_handoff(journal_dir=journal, handoff_dir=output,
                            successor_label="round-002")
    path = next(output.iterdir())
    path.write_text('{"corrupt":true}', encoding="utf-8")
    with pytest.raises(ReceiptError, match="Conflicting"):
        handoff.prepare_handoff(journal_dir=journal, handoff_dir=output,
                                successor_label="round-002")


def test_changed_private_journal_is_refused(dirs):
    journal, output, _ = dirs
    handoff.prepare_handoff(journal_dir=journal, handoff_dir=output,
                            successor_label="round-002")
    path = next(journal.iterdir())
    data = json.loads(path.read_text())
    data["log_id"] = "log_changed"
    path.write_bytes(_canonical(data) + b"\n")
    with pytest.raises(ReceiptError, match="journal decision mismatch"):
        handoff.prepare_handoff(journal_dir=journal, handoff_dir=output,
                                successor_label="round-002")


def test_separate_directories_required(dirs):
    journal, _, _ = dirs
    with pytest.raises(ReceiptError, match="must differ"):
        handoff.prepare_handoff(journal_dir=journal, handoff_dir=journal,
                                successor_label="round-002")
