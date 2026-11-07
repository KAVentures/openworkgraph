"""Executable scoped retention acceptance; no network or provider credentials."""
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from server.open_work_attempt_receipts import (
    append_receipt, list_receipts, purge_expired_receipts,
)

VERDICT = {"status": "unknown", "reason": "provider_unavailable",
           "provider_operation": "unknown", "customer_outcome": "unknown"}


def test_explicit_retention_preserves_recent_and_other_actors(tmp_path):
    path = str(tmp_path / "receipts.sqlite")
    old_id = append_receipt(path, tenant="org", actor="alice", attempt="run", verdict=VERDICT)
    fresh_id = append_receipt(path, tenant="org", actor="alice", attempt="run", verdict=VERDICT)
    other_id = append_receipt(path, tenant="org", actor="bob", attempt="run", verdict=VERDICT)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE attempt_receipts SET checked_at=? WHERE id IN (?,?)",
                   (old, old_id, other_id))
    assert purge_expired_receipts(path, tenant="org", actor="alice", older_than_days=30) == 1
    assert [r["id"] for r in list_receipts(path, tenant="org", actor="alice", attempt="run")] == [fresh_id]
    assert [r["id"] for r in list_receipts(path, tenant="org", actor="bob", attempt="run")] == [other_id]


@pytest.mark.parametrize("window", [0, -1, 3651, True, 1.5, "30"])
def test_invalid_retention_windows_refuse_delete(tmp_path, window):
    path = str(tmp_path / "receipts.sqlite")
    append_receipt(path, tenant="org", actor="alice", attempt="run", verdict=VERDICT)
    with pytest.raises(ValueError):
        purge_expired_receipts(path, tenant="org", actor="alice", older_than_days=window)
    assert len(list_receipts(path, tenant="org", actor="alice", attempt="run")) == 1


def test_naive_clock_refused(tmp_path):
    with pytest.raises(ValueError):
        purge_expired_receipts(str(tmp_path / "r.sqlite"), tenant="org", actor="alice",
                               older_than_days=30, now=datetime(2026, 1, 1))
