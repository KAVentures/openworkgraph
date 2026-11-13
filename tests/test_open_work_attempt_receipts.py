"""Executable local persistence and isolation acceptance for opt-in attempt receipts."""
import pytest

from server.open_work_attempt_receipts import append_receipt, list_receipts, purge_scope


def test_scoped_recheck_history_and_revocation(tmp_path):
    path = str(tmp_path / "receipts.sqlite")
    unknown = {"status": "unknown", "reason": "provider_unavailable",
               "provider_operation": "unknown", "customer_outcome": "unknown"}
    observed = {"status": "verified_provider_event", "event_id": "evt_123",
                "provider_operation": "unknown", "customer_outcome": "unknown"}
    append_receipt(path, tenant="org_a", actor="alice", attempt="run_1", verdict=unknown)
    append_receipt(path, tenant="org_a", actor="alice", attempt="run_1", verdict=observed)
    assert [r["status"] for r in list_receipts(path, tenant="org_a", actor="alice", attempt="run_1")] == [
        "verified_provider_event", "unknown"]
    assert list_receipts(path, tenant="org_b", actor="alice", attempt="run_1") == []
    assert list_receipts(path, tenant="org_a", actor="bob", attempt="run_1") == []
    assert list_receipts(path, tenant="org_a", actor="alice", attempt="run_2") == []
    assert purge_scope(path, tenant="org_a", actor="alice") == 2
    assert list_receipts(path, tenant="org_a", actor="alice", attempt="run_1") == []


def test_refuses_manufactured_customer_outcome_and_invalid_scope(tmp_path):
    path = str(tmp_path / "receipts.sqlite")
    with pytest.raises(ValueError):
        append_receipt(path, tenant="org", actor="actor", attempt="run",
                       verdict={"status": "verified_provider_event",
                                "provider_operation": "unknown", "customer_outcome": "succeeded"})
    with pytest.raises(ValueError):
        append_receipt(path, tenant="org", actor="actor", attempt="../escape",
                       verdict={"status": "unknown", "provider_operation": "unknown",
                                "customer_outcome": "unknown"})
    with pytest.raises(ValueError):
        append_receipt(path, tenant="org", actor="actor", attempt="run",
                       verdict={"status": "unknown", "provider_operation": "succeeded",
                                "customer_outcome": "unknown"})


def test_receipt_journal_rejects_hardlink_without_changing_permissions(tmp_path):
    import os
    import stat
    source = tmp_path / "other.sqlite"
    source.write_text("unrelated private data")
    source.chmod(0o644)
    linked = tmp_path / "receipts.sqlite"
    os.link(source, linked)
    with pytest.raises(ValueError, match="unsafe receipt journal"):
        list_receipts(str(linked), tenant="org", actor="alice", attempt="run")
    assert source.read_text() == "unrelated private data"
    if os.name == "posix":
        assert stat.S_IMODE(source.stat().st_mode) == 0o644


def test_receipt_journal_refuses_existing_permissive_file(tmp_path):
    import os
    import stat
    path = tmp_path / "receipts.sqlite"
    path.touch()
    path.chmod(0o644)
    if os.name == "posix":
        with pytest.raises(ValueError, match="permissions too broad"):
            list_receipts(str(path), tenant="org", actor="alice", attempt="run")
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_receipt_journal_rejects_symlink_and_directory(tmp_path):
    target = tmp_path / "target.sqlite"
    target.write_text("not a journal")
    link = tmp_path / "linked.sqlite"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError):
        list_receipts(str(link), tenant="org", actor="alice", attempt="run")
    with pytest.raises(ValueError, match="unsafe receipt journal"):
        list_receipts(str(tmp_path), tenant="org", actor="alice", attempt="run")


def test_receipt_journal_rejects_preexisting_sqlite_sidecars(tmp_path):
    path = tmp_path / "receipts.sqlite"
    path.touch(mode=0o600)
    for suffix in ("-wal", "-shm"):
        sidecar = tmp_path / ("receipts.sqlite" + suffix)
        sidecar.write_bytes(b"untrusted data")
        with pytest.raises(ValueError, match="unsupported receipt journal sidecar"):
            list_receipts(str(path), tenant="org", actor="alice", attempt="run")
        assert sidecar.read_bytes() == b"untrusted data"
        sidecar.unlink()


def test_receipt_journal_rejects_dangling_sidecar_symlink(tmp_path):
    path = tmp_path / "receipts.sqlite"
    path.touch(mode=0o600)
    sidecar = tmp_path / "receipts.sqlite-wal"
    try:
        sidecar.symlink_to(tmp_path / "missing-target")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="unsupported receipt journal sidecar"):
        list_receipts(str(path), tenant="org", actor="alice", attempt="run")
    assert sidecar.is_symlink()
