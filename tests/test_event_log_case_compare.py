"""Two cases side by side."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_case_compare as cc
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    rows = ["case,activity,time",
            "fast,Open,2024-01-01T00:00:00Z", "fast,Check,2024-01-01T01:00:00Z", "fast,Close,2024-01-01T02:00:00Z",
            "slow,Open,2024-01-02T00:00:00Z", "slow,Escalate,2024-01-02T01:00:00Z", "slow,Check,2024-01-02T09:00:00Z",
            "slow,Close,2024-01-02T10:00:00Z"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="c", source_format="csv",
                                mapping={"case_column": "case", "activity_column": "activity",
                                         "timestamp_column": "time"})["log_id"]


def test_shared_and_extra_steps_and_where_time_grew():
    r = cc.compare_cases(_log(), "fast", "slow")
    assert [(s["activity"], s["kind"]) for s in r["steps"]] == [
        ("Open", "both"), ("Escalate", "only_b"), ("Check", "both"), ("Close", "both")]
    assert r["case_a"]["hours"] == 2 and r["case_b"]["hours"] == 10
    assert r["largest_gap_growth"] == {"to": "Check", "from": "Open", "extra_hours": 8}
    assert r["only_b"] == 1 and r["shared_steps"] == 3


def test_refusals():
    log = _log()
    with pytest.raises(EventLogWorkspaceError, match="different"):
        cc.compare_cases(log, "fast", "fast")
    with pytest.raises(EventLogWorkspaceError, match="no case"):
        cc.compare_cases(log, "fast", "nope")
