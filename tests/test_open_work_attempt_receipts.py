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

@pytest.mark.parametrize("verdict", [
    {"status": "verified_provider_operation", "provider_operation": "unknown",
     "event_id": "evt_1", "customer_outcome": "unknown"},
    {"status": "verified_provider_event", "provider_operation": "succeeded",
     "event_id": "evt_1", "customer_outcome": "unknown"},
    {"status": "verified_provider_event", "provider_operation": "unknown",
     "customer_outcome": "unknown"},
    {"status": "unknown", "provider_operation": "unknown",
     "event_id": "evt_1", "customer_outcome": "unknown"},
    {"status": "unknown", "provider_operation": "unknown",
     "customer_outcome": "unknown", "provider_token": "secret"},
])
def test_receipt_journal_rejects_inconsistent_provider_claims(tmp_path, verdict):
    path = str(tmp_path / "receipts.sqlite")
    with pytest.raises(ValueError):
        append_receipt(path, tenant="org", actor="actor", attempt="run", verdict=verdict)
    if (tmp_path / "receipts.sqlite").exists():
        assert list_receipts(path, tenant="org", actor="actor", attempt="run") == []


@pytest.mark.parametrize("source_checked", [
    "not-a-date", "2026-11-13T12:00:00", 12345,
    "x" * 65, "2026-13-99T12:00:00+00:00",
])
def test_receipt_journal_refuses_untrustworthy_verifier_timestamp(tmp_path, source_checked):
    path = str(tmp_path / "receipts.sqlite")
    with pytest.raises(ValueError, match="verification timestamp"):
        append_receipt(
            path, tenant="org", actor="actor", attempt="run",
            verdict={"status": "verified_provider_event",
                     "provider_operation": "unknown",
                     "customer_outcome": "unknown", "event_id": "evt_1",
                     "checked_at": source_checked},
        )
    if (tmp_path / "receipts.sqlite").exists():
        assert list_receipts(path, tenant="org", actor="actor", attempt="run") == []


def test_receipt_journal_accepts_timezone_aware_verifier_timestamp(tmp_path):
    path = str(tmp_path / "receipts.sqlite")
    append_receipt(
        path, tenant="org", actor="actor", attempt="run",
        verdict={"status": "verified_provider_event",
                 "provider_operation": "unknown",
                 "customer_outcome": "unknown", "event_id": "evt_1",
                 "checked_at": "2026-11-13T11:00:00+00:00"},
    )
    rows = list_receipts(path, tenant="org", actor="actor", attempt="run")
    assert len(rows) == 1
    assert rows[0]["customer_outcome"] == "unknown"


def test_source_revocation_purges_all_actors_but_not_other_tenants(tmp_path):
    from server.open_work_attempt_receipts import purge_tenant
    path = str(tmp_path / "receipts.sqlite")
    unknown = {"status": "unknown", "reason": "provider_unavailable",
               "provider_operation": "unknown", "customer_outcome": "unknown"}
    for tenant, actor in (("source_a", "alice"), ("source_a", "bob"),
                          ("source_b", "alice")):
        append_receipt(path, tenant=tenant, actor=actor,
                       attempt="attempt_1", verdict=unknown)
    assert purge_tenant(path, tenant="source_a") == 2
    assert list_receipts(path, tenant="source_a", actor="alice", attempt="attempt_1") == []
    assert list_receipts(path, tenant="source_a", actor="bob", attempt="attempt_1") == []
    assert len(list_receipts(path, tenant="source_b", actor="alice", attempt="attempt_1")) == 1
    assert purge_tenant(path, tenant="source_a") == 0
    with pytest.raises(ValueError):
        purge_tenant(path, tenant="../source_b")


def test_receipt_journal_refuses_leftover_rollback_data(tmp_path):
    """A stale rollback file may retain deleted or revoked provider evidence."""
    path = tmp_path / "receipts.sqlite"
    path.touch(mode=0o600)
    rollback = tmp_path / "receipts.sqlite-journal"
    rollback.write_bytes(b"previously revoked provider evidence")
    with pytest.raises(ValueError, match="unsupported receipt rollback journal"):
        list_receipts(str(path), tenant="org", actor="alice", attempt="run")
    assert rollback.read_bytes() == b"previously revoked provider evidence"


def test_receipt_journal_refuses_dangling_rollback_symlink(tmp_path):
    path = tmp_path / "receipts.sqlite"
    path.touch(mode=0o600)
    rollback = tmp_path / "receipts.sqlite-journal"
    try:
        rollback.symlink_to(tmp_path / "missing-rollback")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="unsupported receipt rollback journal"):
        list_receipts(str(path), tenant="org", actor="alice", attempt="run")
    assert rollback.is_symlink()



def test_expired_receipt_cleanup_is_actor_scoped_and_uses_strict_cutoff(tmp_path):
    """Retention physically removes expired evidence, not other users' rows."""
    import sqlite3
    from datetime import datetime, timezone
    from server.open_work_attempt_receipts import purge_expired_receipts

    path = str(tmp_path / "receipts.sqlite")
    verdict = {"status": "unknown", "provider_operation": "unknown",
               "customer_outcome": "unknown"}
    for tenant, actor in (("org_a", "alice"), ("org_a", "bob"),
                          ("org_b", "alice")):
        append_receipt(path, tenant=tenant, actor=actor,
                       attempt="run_1", verdict=verdict)
    # Backdate the first actor's receipt, and leave the others untouched.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE attempt_receipts SET checked_at=? "
                   "WHERE tenant=? AND actor=?",
                   ("2026-11-01T00:00:00+00:00", "org_a", "alice"))
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    assert purge_expired_receipts(
        path, tenant="org_a", actor="alice", older_than_days=7, now=now) == 1
    assert list_receipts(path, tenant="org_a", actor="alice", attempt="run_1") == []
    assert len(list_receipts(path, tenant="org_a", actor="bob", attempt="run_1")) == 1
    assert len(list_receipts(path, tenant="org_b", actor="alice", attempt="run_1")) == 1
    assert purge_expired_receipts(
        path, tenant="org_a", actor="alice", older_than_days=7, now=now) == 0


def test_expired_receipt_cleanup_rejects_invalid_policy_without_deleting(tmp_path):
    from server.open_work_attempt_receipts import purge_expired_receipts

    path = str(tmp_path / "receipts.sqlite")
    append_receipt(path, tenant="org", actor="alice", attempt="run",
                   verdict={"status": "unknown", "provider_operation": "unknown",
                            "customer_outcome": "unknown"})
    for invalid in (0, -1, 3651, True, "7"):
        with pytest.raises(ValueError, match="retention window"):
            purge_expired_receipts(
                path, tenant="org", actor="alice", older_than_days=invalid)
    assert len(list_receipts(path, tenant="org", actor="alice", attempt="run")) == 1


def test_retention_overwrites_deleted_receipt_bytes_not_only_sql_rows(tmp_path):
    """SQLite secure_delete must erase receipt evidence from freed pages."""
    import sqlite3
    from datetime import datetime, timezone
    from server.open_work_attempt_receipts import purge_expired_receipts

    path = tmp_path / "receipts.sqlite"
    marker = "sensitive_event_OWG_ERASURE_7f3d9b42"
    append_receipt(
        str(path), tenant="org", actor="alice", attempt="run",
        verdict={"status": "verified_provider_event", "event_id": marker,
                 "provider_operation": "unknown", "customer_outcome": "unknown"},
    )
    assert marker.encode() in path.read_bytes()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE attempt_receipts SET checked_at=? WHERE tenant=? AND actor=?",
                   ("2026-11-01T00:00:00+00:00", "org", "alice"))
    assert purge_expired_receipts(
        str(path), tenant="org", actor="alice", older_than_days=7,
        now=datetime(2026, 11, 18, tzinfo=timezone.utc),
    ) == 1
    assert list_receipts(str(path), tenant="org", actor="alice", attempt="run") == []
    assert marker.encode() not in path.read_bytes()


def test_consent_revocation_overwrites_receipt_bytes(tmp_path):
    """Actor revocation must not leave provider event IDs in SQLite free space."""
    path = tmp_path / "receipts.sqlite"
    marker = "sensitive_event_OWG_REVOKE_2d91e4f7"
    append_receipt(
        str(path), tenant="org", actor="alice", attempt="run",
        verdict={"status": "verified_provider_event", "event_id": marker,
                 "provider_operation": "unknown", "customer_outcome": "unknown"},
    )
    assert marker.encode() in path.read_bytes()
    assert purge_scope(str(path), tenant="org", actor="alice") == 1
    assert marker.encode() not in path.read_bytes()
