"""Signals on due-date adherence."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_signals as signals
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "attribute_columns": ["due"]}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()


def _log():
    rows = ["case,activity,time,due"]
    for i in range(6):   # paid on time
        rows += [f"ok{i},Invoice,2024-03-01T09:00:00Z,2024-03-10", f"ok{i},Pay,2024-03-05T09:00:00Z,"]
    for i in range(2):   # paid late
        rows += [f"late{i},Invoice,2024-03-01T09:00:00Z,2024-03-03", f"late{i},Pay,2024-03-08T09:00:00Z,"]
    for i in range(3):   # still open, overdue by the end of the log
        rows += [f"open{i},Invoice,2024-03-02T09:00:00Z,2024-03-12", f"open{i},Remind,2024-03-13T09:00:00Z,"]
    rows += ["last,Invoice,2024-03-20T09:00:00Z,2024-04-30"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="d", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_overdue_and_late_share_signals():
    log = _log()
    signals.add_rule(log, {"name": "Overdue invoices", "when": {
        "kind": "due_date", "activity": "Pay", "due_attribute": "due", "metric": "open_overdue_cases", "threshold": 2}})
    signals.add_rule(log, {"name": "Late payments", "when": {
        "kind": "due_date", "activity": "Pay", "due_attribute": "due", "metric": "late_share", "threshold": 0.3}})
    overdue, late = signals.evaluate(log)["signals"]
    assert overdue["value"] == 3 and overdue["triggered"] and overdue["due_dates"]["open_not_due"] == 1
    assert late["value"] == 0.25 and late["triggered"] is False


def test_bad_due_date_rules():
    log = _log()
    for when in ({"activity": "Pay", "metric": "late_share", "threshold": 0.2},
                 {"activity": "Pay", "due_attribute": "due", "due_days": 3, "metric": "late_share", "threshold": 0.2},
                 {"activity": "Pay", "due_days": 3, "metric": "late_share", "threshold": 2}):
        with pytest.raises(EventLogWorkspaceError):
            signals.add_rule(log, {"name": "x", "when": {"kind": "due_date", **when}})
