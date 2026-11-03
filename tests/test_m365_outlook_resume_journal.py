"""Production-journal integration boundaries: real private files and replay safety."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from server.m365_outlook_owg_receipt import ReceiptError
from server.m365_outlook_resume_journal import record_resume, FORMAT


def _decision():
    return {
        "status": "resume_authorized_for_operator",
        "candidate_sha256": "a" * 64,
        "log_id": "log_0123456789abcdef",
        "events_verified_by_count": 2,
    }


def _private_dir(tmp_path):
    directory = tmp_path / "journal"
    directory.mkdir(mode=0o700)
    if os.name != "nt":
        directory.chmod(0o700)
    return directory


def test_verified_decision_persists_and_replay_is_idempotent(tmp_path):
    directory = _private_dir(tmp_path)
    with patch("server.m365_outlook_resume_journal.authorize_resume", return_value=_decision()) as gate:
        first = record_resume(journal_dir=directory, successor_label="round-002")
        second = record_resume(journal_dir=directory, successor_label="round-002")
    assert gate.call_count == 2  # live inventory revalidated even on replay
    assert first["format"] == FORMAT
    assert first["checkpoint_advanced"] is False
    assert first["provider_requests"] == 0
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    files = list(directory.glob("resume-*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["successor_label"] == "round-002"
    if os.name != "nt":
        assert files[0].stat().st_mode & 0o077 == 0


def test_conflicting_successor_is_refused_without_replacement(tmp_path):
    directory = _private_dir(tmp_path)
    with patch("server.m365_outlook_resume_journal.authorize_resume", return_value=_decision()):
        record_resume(journal_dir=directory, successor_label="round-002")
        with pytest.raises(ReceiptError, match="Conflicting"):
            record_resume(journal_dir=directory, successor_label="round-003")
    assert len(list(directory.iterdir())) == 1


def test_no_record_on_revocation_or_missing_review(tmp_path):
    directory = _private_dir(tmp_path)
    with patch("server.m365_outlook_resume_journal.authorize_resume",
               side_effect=ReceiptError("Revoked")):
        with pytest.raises(ReceiptError, match="Revoked"):
            record_resume(journal_dir=directory, successor_label="round-002")
    assert not list(directory.iterdir())


@pytest.mark.parametrize("label", ["", "../other", ".", "two words", "x" * 97])
def test_unsafe_successor_refused_before_authorization(tmp_path, label):
    directory = _private_dir(tmp_path)
    with patch("server.m365_outlook_resume_journal.authorize_resume") as gate:
        with pytest.raises(ReceiptError):
            record_resume(journal_dir=directory, successor_label=label)
    gate.assert_not_called()


def test_corrupted_existing_record_never_overwritten(tmp_path):
    directory = _private_dir(tmp_path)
    with patch("server.m365_outlook_resume_journal.authorize_resume", return_value=_decision()):
        record_resume(journal_dir=directory, successor_label="round-002")
        file = next(directory.iterdir())
        file.write_text("corrupt")
        with pytest.raises(ReceiptError, match="unreadable"):
            record_resume(journal_dir=directory, successor_label="round-002")
        assert file.read_text() == "corrupt"
