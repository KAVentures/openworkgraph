"""Durable checkpoint restart contract using the production ledger implementation.

Only upstream handoff and authenticated membership are simulated; the actual
checkpoint writer, ledger chain validation, grant checks and restart reads run.
This is not a live Microsoft tenant acceptance test.
"""
import hashlib
import json
import os
from unittest.mock import patch

import pytest

from server.m365_outlook_checkpoint import commit_checkpoint, inspect_checkpoint
from server.m365_outlook_owg_receipt import ReceiptError


def private_dir(path):
    path.mkdir()
    path.chmod(0o700)
    return path


def test_two_generations_persist_across_restart_and_reject_revoked_grant(tmp_path):
    tenant, mailbox, folder = "tenant", "mailbox", "selected-folder"
    ledger = private_dir(tmp_path / "ledger")
    artifacts = private_dir(tmp_path / "artifacts")
    journal = private_dir(tmp_path / "journal")
    handoff = private_dir(tmp_path / "handoff")
    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": tenant, "mailbox_scope": mailbox, "folder_id": folder,
    }))
    grant.chmod(0o600)
    scope = dict(tenant=tenant, mailbox=mailbox, folder=folder,
                 grant_file=grant, ledger_dir=ledger)
    previous = None
    for generation in (1, 2):
        csv_path = artifacts / f"round-{generation}.csv"
        csv_path.write_text(f"event-{generation}\n")
        csv_path.chmod(0o600)
        digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        candidate = artifacts / f"round-{generation}.json"
        candidate.write_text(json.dumps({"csv_name": csv_path.name,
                                         "csv_sha256": digest}))
        candidate.chmod(0o600)
        cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/"
                  f"{folder}/messages/delta?%24deltatoken=round-{generation}")
        with (
            patch("server.m365_outlook_checkpoint._candidate", return_value=cursor),
            patch("server.m365_outlook_checkpoint.prepare_handoff",
                  return_value={"idempotent_replay": True}) as verified_handoff,
            patch("server.m365_outlook_checkpoint.reconcile_membership",
                  return_value={"status": "all_candidate_events_present"}) as membership,
        ):
            result = commit_checkpoint(
                **scope, log_id="log-1", confirm_log_id="log-1",
                candidate=candidate, csv_path=csv_path, receipt_file=artifacts / "receipt",
                journal_dir=journal, handoff_dir=handoff, successor_label="next",
                expected_previous_candidate_sha256=previous,
                consent_local_read=True, confirm_removals_reviewed=True,
                confirm_checkpoint_commit=True, session="valid")
        assert result["status"] == "checkpoint_committed"
        assert result["generation"] == generation
        assert result["idempotent_replay"] is False
        verified_handoff.assert_called_once()
        membership.assert_called_once()
        # Fresh call reloads and verifies the entire chain from disk.
        inspected = inspect_checkpoint(**scope, consent_local_read=True)
        assert inspected["generation"] == generation
        assert inspected["candidate_sha256"] == digest
        assert inspected["cursor_url"] == cursor
        previous = digest

    files = sorted(ledger.glob("checkpoint-*.json"))
    assert len(files) == 2
    # POSIX permission bits are not enforced by the Windows filesystem.\n    # The durable ledger and grant-revocation assertions still run on every OS.\n    if os.name == "posix":\n        assert all((p.stat().st_mode & 0o077) == 0 for p in files)
    grant.write_text(json.dumps({
        "version": 1, "enabled": False, "consented": False,
        "tenant_scope": tenant, "mailbox_scope": mailbox, "folder_id": folder,
    }))
    grant.chmod(0o600)
    with pytest.raises((ReceiptError, ValueError)):
        inspect_checkpoint(**scope, consent_local_read=True)
    assert len(list(ledger.glob("checkpoint-*.json"))) == 2
