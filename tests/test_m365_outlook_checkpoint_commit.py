"""Contract tests for the operator-controlled M365 checkpoint commit."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from server.m365_outlook_checkpoint_commit import commit_checkpoint, FORMAT
from server.m365_outlook_owg_receipt import ReceiptError

LOG = "log_0123456789abcdef"
CURSOR = "https://graph.microsoft.com/v1.0/me/mailFolders/folder/messages/delta?%24deltatoken=abc"


def _run(folder, **overrides):
    options = dict(
        tenant="tenant", mailbox="mailbox", folder="folder",
        grant_file=folder / "grant.json", candidate=folder / "round.json",
        csv_path=folder / "round.csv", receipt_file=folder / "receipt-a.json",
        checkpoint_file=folder / "checkpoint-a.json", log_id=LOG,
        confirm_log_id=LOG, port=8765, session="test-session",
        consent_local_read=True, confirm_removals_reviewed=True,
        consent_checkpoint_commit=True,
    )
    options.update(overrides)
    return commit_checkpoint(**options)


@pytest.fixture
def private_folder():
    with TemporaryDirectory() as temp:
        path = Path(temp)
        os.chmod(path, 0o700)
        (path / "round.json").write_bytes(b'{"candidate":"test"}')
        os.chmod(path / "round.json", 0o600)
        yield path


@pytest.fixture
def verified():
    with (patch("server.m365_outlook_checkpoint_commit._grant"),
          patch("server.m365_outlook_checkpoint_commit._artifact",
                return_value=(b"csv", {"csv_sha256": "a" * 64})),
          patch("server.m365_outlook_checkpoint_commit._candidate", return_value=CURSOR),
          patch("server.m365_outlook_checkpoint_commit.authorize_resume",
                return_value={"status": "resume_authorized_for_operator"})):
        yield


def test_persists_private_cursor_without_exposing_it(private_folder, verified):
    result = _run(private_folder)
    assert result["status"] == "checkpoint_committed"
    assert CURSOR not in json.dumps(result)
    path = private_folder / "checkpoint-a.json"
    stored = json.loads(path.read_text())
    assert stored["cursor_url"] == CURSOR
    assert stored["log_id"] == LOG
    assert stored["event_level_proof"] is False
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0


def test_requires_explicit_checkpoint_consent(private_folder, verified):
    with pytest.raises(ReceiptError):
        _run(private_folder, consent_checkpoint_commit=False)
    assert not (private_folder / "checkpoint-a.json").exists()


def test_requires_separate_read_and_removal_review(private_folder, verified):
    # The live authorization gate owns validation of both flags.
    with patch("server.m365_outlook_checkpoint_commit.authorize_resume",
               side_effect=ReceiptError("No consent")):
        with pytest.raises(ReceiptError):
            _run(private_folder, consent_local_read=False)
    assert not (private_folder / "checkpoint-a.json").exists()


def test_refuses_second_commit_without_overwrite(private_folder, verified):
    _run(private_folder)
    original = (private_folder / "checkpoint-a.json").read_bytes()
    with pytest.raises(ReceiptError):
        _run(private_folder)
    assert (private_folder / "checkpoint-a.json").read_bytes() == original


def test_refuses_symlink_destination(private_folder, verified):
    path = private_folder / "checkpoint-a.json"
    path.symlink_to(private_folder / "round.json")
    with pytest.raises(ReceiptError):
        _run(private_folder)


def test_refuses_unverified_live_inventory(private_folder, verified):
    with patch("server.m365_outlook_checkpoint_commit.authorize_resume",
               side_effect=ReceiptError("Inventory changed")):
        with pytest.raises(ReceiptError):
            _run(private_folder)
    assert not (private_folder / "checkpoint-a.json").exists()


def test_revocation_before_persist_refuses(private_folder, verified):
    with patch("server.m365_outlook_checkpoint_commit._grant",
               side_effect=[None, ReceiptError("Revoked")]):
        with pytest.raises(ReceiptError):
            _run(private_folder)
    assert not (private_folder / "checkpoint-a.json").exists()


def test_invalid_destination_refuses(private_folder, verified):
    with pytest.raises(ReceiptError):
        _run(private_folder, checkpoint_file=private_folder / "other.json")
