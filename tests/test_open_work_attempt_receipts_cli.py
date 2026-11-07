"""End-to-end offline CLI checks: scoped history, retention and revocation."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from server.open_work_attempt_receipts import append_receipt, list_receipts
from server.open_work_attempt_receipts_cli import main


VERDICT = {"status": "verified_provider_event", "provider_operation": "unknown",
           "customer_outcome": "unknown", "event_id": "evt_1"}


def test_history_and_purge_are_exact_actor_scoped(tmp_path, capsys):
    path = str(tmp_path / "journal.sqlite3")
    append_receipt(path, tenant="org1", actor="alice", attempt="run1", verdict=VERDICT)
    append_receipt(path, tenant="org1", actor="bob", attempt="run1", verdict=VERDICT)
    append_receipt(path, tenant="org2", actor="alice", attempt="run1", verdict=VERDICT)
    base = ["--journal-path", path, "--tenant", "org1", "--actor", "alice"]
    assert main(base + ["history", "--attempt", "run1"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["receipts"]) == 1
    assert data["receipts"][0]["customer_outcome"] == "unknown"
    assert main(base + ["purge", "--confirm-delete"]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"] == 1
    assert list_receipts(path, tenant="org1", actor="alice", attempt="run1") == []
    assert len(list_receipts(path, tenant="org1", actor="bob", attempt="run1")) == 1
    assert len(list_receipts(path, tenant="org2", actor="alice", attempt="run1")) == 1


def test_retention_deletes_only_expired_receipts(tmp_path, capsys):
    path = str(tmp_path / "journal.sqlite3")
    for actor in ("alice", "bob"):
        append_receipt(path, tenant="org1", actor=actor, attempt="run1", verdict=VERDICT)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE attempt_receipts SET checked_at=? WHERE actor='alice'",
                   ((datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),))
    base = ["--journal-path", path, "--tenant", "org1", "--actor", "alice"]
    assert main(base + ["retention", "--older-than-days", "30", "--confirm-delete"]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"] == 1
    assert list_receipts(path, tenant="org1", actor="alice", attempt="run1") == []
    assert len(list_receipts(path, tenant="org1", actor="bob", attempt="run1")) == 1


def test_refuses_invalid_scope_and_retention_without_deleting(tmp_path, capsys):
    path = str(tmp_path / "journal.sqlite3")
    append_receipt(path, tenant="org1", actor="alice", attempt="run1", verdict=VERDICT)
    base = ["--journal-path", path, "--tenant", "org1", "--actor", "alice"]
    assert main(base + ["retention", "--older-than-days", "0", "--confirm-delete"]) == 2
    assert "invalid_scope_or_journal" in capsys.readouterr().err
    assert main(["--journal-path", path, "--tenant", "../org1", "--actor", "alice",
                 "history", "--attempt", "run1"]) == 2
    assert len(list_receipts(path, tenant="org1", actor="alice", attempt="run1")) == 1
