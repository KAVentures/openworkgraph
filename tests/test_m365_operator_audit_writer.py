"""Contract tests for durable M365 operator audit receipts and CLI uncertainty."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from server.m365_operator_audit_writer import record_operator_phase, AuditWriteError
from server.m365_operator_audit_routes import read_operator_audit
from server import m365_outlook_operator_cycle as cycle


@pytest.mark.skipif(os.name != "posix", reason="Private POSIX audit storage")
def test_operator_review_to_dashboard_receipt(tmp_path):
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    record_operator_phase("extract", {
        "status": "review_required", "event_snapshots": 3,
        "removal_markers_for_review": 1,
        "cursor_url": "DO-NOT-STORE", "token": "SECRET"},
        directory=directory, tenant="tenant-secret", mailbox="mailbox-secret",
        folder="folder-secret", log_id="private-log")
    result = read_operator_audit(directory)
    assert result["records"][0]["action"] == "extract"
    assert result["records"][0]["event_snapshots"] == 3
    assert result["records"][0]["removal_markers_for_review"] == 1
    raw = next(directory.iterdir()).read_text()
    for secret in ("DO-NOT-STORE", "SECRET", "tenant-secret",
                   "mailbox-secret", "folder-secret", "private-log"):
        assert secret not in raw
    assert result["provider_validated"] is False


@pytest.mark.skipif(os.name != "posix", reason="Private POSIX audit storage")
def test_commit_and_append_audit_are_separate(tmp_path):
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    for phase, result in (("append", {"status": "receipt_persisted", "events_after": 2}),
                          ("checkpoint-commit", {"status": "checkpoint_committed", "generation": 2})):
        record_operator_phase(phase, result, directory=directory,
                              tenant="t", mailbox="m", folder="f", log_id="L")
    records = read_operator_audit(directory)["records"]
    assert {item["action"] for item in records} == {"append", "commit"}
    assert next(item for item in records if item["action"] == "commit")["committed_generation"] == 2


@pytest.mark.skipif(os.name != "posix", reason="Private POSIX audit storage")
def test_refuses_symlink_or_world_readable_directory(tmp_path):
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    for path in (alias, Path("relative")):
        with pytest.raises(AuditWriteError):
            record_operator_phase("append", {"status": "receipt_persisted"}, directory=path,
                                  tenant="t", mailbox="m", folder="f")
    directory.chmod(0o755)
    with pytest.raises(AuditWriteError):
        record_operator_phase("append", {"status": "receipt_persisted"}, directory=directory,
                              tenant="t", mailbox="m", folder="f")


def test_audit_failure_after_append_is_uncertain_not_retryable(capsys, monkeypatch):
    monkeypatch.setenv("OWG_M365_AUDIT_DIR", "/private/audit")
    args = ["append", "--tenant", "t", "--mailbox", "m", "--folder", "f",
            "--grant-file", "grant", "--log-id", "L", "--confirm-log-id", "L",
            "--candidate", "candidate", "--csv-path", "csv",
            "--receipt-file", "receipt", "--consent-local-read",
            "--consent-local-append", "--confirm-candidate-reviewed"]
    with patch.object(cycle, "append", return_value={"status": "receipt_persisted",
                                                     "log_id": "L", "events_after": 1}), \
         patch.object(cycle, "record_operator_phase", side_effect=AuditWriteError("failure")):
        assert cycle.main(args) == 3
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "audit_uncertain"
    assert response["retry_allowed"] is False


@pytest.mark.skipif(os.name != "posix", reason="Private POSIX audit storage")
def test_audit_never_fabricates_success_from_failed_operation(tmp_path):
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    for phase, status in (("append", "unverified"), ("extract", "refused"),
                          ("checkpoint-commit", "manual_reconciliation_required"),
                          ("review", "refused")):
        with pytest.raises(AuditWriteError):
            record_operator_phase(phase, {"status": status}, directory=directory,
                                  tenant="t", mailbox="m", folder="f")
    assert list(directory.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="Private POSIX audit storage")
def test_operator_cycle_receipt_status_is_preserved(tmp_path):
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    record_operator_phase("append", {"status": "receipt_persisted"}, directory=directory,
                          tenant="t", mailbox="m", folder="f")
    assert read_operator_audit(directory)["records"][0]["status"] == "receipt_persisted"


def test_non_posix_audit_refuses_before_append(monkeypatch, capsys):
    monkeypatch.setenv("OWG_M365_AUDIT_DIR", "/private/audit")
    with patch.object(cycle.os, "name", "nt"), patch.object(cycle, "append") as mutation:
        code = cycle.main(["append", "--tenant", "t", "--mailbox", "m",
                           "--folder", "f", "--grant-file", "grant",
                           "--log-id", "L", "--confirm-log-id", "L",
                           "--candidate", "candidate", "--csv-path", "csv",
                           "--receipt-file", "receipt"])
    assert code == 2
    mutation.assert_not_called()
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
