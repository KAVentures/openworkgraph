"""Milestone funnel over an imported event log."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_funnel as fn
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(cases):
    """``cases`` is a list of [(activity, hour offset), …]; case i starts on day i."""
    rows = ["case,activity,time"]
    for i, steps in enumerate(cases):
        for activity, hour in steps:
            day, h = divmod(i * 24 + hour, 24)
            rows.append(f"c{i},{activity},2024-01-{1 + day:02d}T{h:02d}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="f", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_steps_conversion_timing_and_stops():
    full = [("Lead", 0), ("Call", 2), ("Offer", 6), ("Won", 10)]
    cases = [full] * 6 + [[("Lead", 0), ("Call", 4), ("Lost", 5)]] * 3 + [[("Lead", 0), ("Spam", 1)]] * 2 \
        + [[("Offer", 0), ("Lead", 1), ("Call", 2)]]  # Offer before Lead does not count for step 3
    log = _import(cases)
    r = fn.funnel(log, ["Lead", "Call", "Offer", "Won"])
    assert r["cases"] == 12
    assert [s["cases"] for s in r["steps"]] == [12, 10, 6, 6]
    lead, call, offer, won = r["steps"]
    assert call["conversion_from_previous"] == round(10 / 12, 4) and call["dropped_after_previous"] == 2
    assert call["stopped_cases_last_activity"] == [{"activity": "Spam", "cases": 2}]
    assert offer["dropped_after_previous"] == 4
    assert offer["stopped_cases_last_activity"] == [{"activity": "Lost", "cases": 3}, {"activity": "Call", "cases": 1}]
    assert offer["hours_from_previous"]["median"] == 4 and call["hours_from_previous"]["median"] == 2
    assert won["share_of_first_step"] == 0.5 and r["completed"]["hours_first_to_last"]["median"] == 10
    assert r["largest_drop"] == {"after_step": 2, "to_step": 3, "cases": 4}


def test_conversion_window_restarts_on_a_later_previous_step():
    cases = [[("Apply", 0), ("Apply", 30), ("Approve", 32)],   # second Apply restarts the clock: within 24h
             [("Apply", 0), ("Approve", 30)],                  # 30h later: outside the window
             [("Apply", 0), ("Approve", 5)]]
    log = _import(cases)
    assert [s["cases"] for s in fn.funnel(log, ["Apply", "Approve"])["steps"]] == [3, 3]
    windowed = fn.funnel(log, ["Apply", "Approve"], within_hours=24)
    assert [s["cases"] for s in windowed["steps"]] == [3, 2]
    assert windowed["steps"][1]["hours_from_previous"]["median"] == 3.5


def test_bad_requests_are_refused():
    log = _import([[("A", 0), ("B", 1)]])
    for steps in (["A"], ["A", "B"] * 5):
        with pytest.raises(EventLogWorkspaceError, match="steps"):
            fn.funnel(log, steps)
    with pytest.raises(EventLogWorkspaceError, match="not in this selection: Z"):
        fn.funnel(log, ["A", "Z"])
    with pytest.raises(EventLogWorkspaceError, match="window"):
        fn.funnel(log, ["A", "B"], within_hours=0)
    for steps in ("AB", {"A": 1, "B": 2}, ["A", None], ["A", 1]):
        with pytest.raises(EventLogWorkspaceError):
            fn.funnel(log, steps)
