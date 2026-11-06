"""Acceptance of durable scoped Microsoft 365 checkpoint state transitions."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from server.m365_outlook_checkpoint import (
    commit_checkpoint, inspect_checkpoint, _entries, FORMAT,
)
from server.m365_outlook_delta import _scope
from server.m365_outlook_owg_receipt import ReceiptError


def private_dir(path):
    path.mkdir()
    os.chmod(path, 0o700)
    return path


@pytest.fixture
def setup(tmp_path):
    ledger = private_dir(tmp_path / "ledger")
    candidate_dir = private_dir(tmp_path / "candidate")
    journal = private_dir(tmp_path / "journal")
    handoff = private_dir(tmp_path / "handoff")
    candidate = candidate_dir / "round.json"
    csv_path = candidate_dir / "round.csv"
    csv_path.write_text("case_id,activity,timestamp,source_event_id\n")
    os.chmod(csv_path, 0o600)
    import hashlib
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    record = {"csv_name": "round.csv", "csv_sha256": digest}
    candidate.write_text(json.dumps(record))
    os.chmod(candidate, 0o600)
    grant = tmp_path / "grant.json"
    grant.write_text("{}")
    os.chmod(grant, 0o600)
    kwargs = dict(
        tenant="tenant", mailbox="mailbox", folder="folder",
        log_id="log", confirm_log_id="log", grant_file=grant,
        candidate=candidate, csv_path=csv_path, receipt_file=tmp_path / "receipt",
        journal_dir=journal, handoff_dir=handoff, successor_label="round2",
        ledger_dir=ledger, consent_local_read=True,
        confirm_removals_reviewed=True, confirm_checkpoint_commit=True)
    return kwargs, record


def mock_gates(record):
    return (
        patch("server.m365_outlook_checkpoint._candidate",
              return_value="https://graph.microsoft.com/v1.0/me/mailFolders/folder/messages/delta?%24deltatoken=abc"),
        patch("server.m365_outlook_checkpoint._grant"),
        patch("server.m365_outlook_checkpoint.prepare_handoff",
              return_value={"idempotent_replay": True}),
        patch("server.m365_outlook_checkpoint.reconcile_membership",
              return_value={"status": "all_candidate_events_present"}),
    )


def test_first_commit_replay_restart_and_successor(setup):
    kwargs, record = setup
    from contextlib import ExitStack
    with ExitStack() as stack:
        for context in mock_gates(record):
            stack.enter_context(context)
        first = commit_checkpoint(**kwargs)
        assert first["generation"] == 1
        assert first["idempotent_replay"] is False
        assert commit_checkpoint(**kwargs)["idempotent_replay"] is True
        recovered = inspect_checkpoint(
            tenant="tenant", mailbox="mailbox", folder="folder",
            grant_file=kwargs["grant_file"], ledger_dir=kwargs["ledger_dir"],
            consent_local_read=True)
        assert recovered["candidate_sha256"] == record["csv_sha256"]
        assert recovered["generation"] == 1
        assert recovered["checkpoint_advanced"] is True
        assert recovered["provider_requests"] == 0
        assert len(_entries(kwargs["ledger_dir"], _scope("tenant", "mailbox", "folder"))) == 1


def test_no_consent_no_commit(setup):
    kwargs, _ = setup
    kwargs["confirm_checkpoint_commit"] = False
    with pytest.raises(ReceiptError):
        commit_checkpoint(**kwargs)
    assert list(kwargs["ledger_dir"].iterdir()) == []


def test_unverified_membership_refuses_without_checkpoint(setup):
    kwargs, record = setup
    from contextlib import ExitStack
    with ExitStack() as stack:
        for context in mock_gates(record)[:3]:
            stack.enter_context(context)
        with patch("server.m365_outlook_checkpoint.reconcile_membership",
                   return_value={"status": "manual_reconciliation_required"}):
            with pytest.raises(ReceiptError):
                commit_checkpoint(**kwargs)
    assert list(kwargs["ledger_dir"].iterdir()) == []


def test_tamper_and_cross_scope_refused(setup):
    kwargs, record = setup
    from contextlib import ExitStack
    with ExitStack() as stack:
        for context in mock_gates(record):
            stack.enter_context(context)
        commit_checkpoint(**kwargs)
        assert inspect_checkpoint(tenant="other", mailbox="mailbox", folder="folder",
                                  grant_file=kwargs["grant_file"],
                                  ledger_dir=kwargs["ledger_dir"],
                                  consent_local_read=True) is None
        entry = next(kwargs["ledger_dir"].iterdir())
        value = json.loads(entry.read_text())
        value["event_membership_verified"] = False
        entry.write_text(json.dumps(value))
        os.chmod(entry, 0o600)
        with pytest.raises(ReceiptError):
            inspect_checkpoint(tenant="tenant", mailbox="mailbox", folder="folder",
                               grant_file=kwargs["grant_file"],
                               ledger_dir=kwargs["ledger_dir"],
                               consent_local_read=True)
