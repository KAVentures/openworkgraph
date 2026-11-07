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
