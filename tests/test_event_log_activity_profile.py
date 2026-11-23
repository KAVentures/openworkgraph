"""Activity profile."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_activity_profile as ap
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def test_profile_of_a_reworked_step():
    rows = ["case,activity,time"]
    # 2024-01-01 is a Monday. 6 cases check once; 2 check twice (with Fix between); 2 skip Check.
    for c in range(10):
        if c < 6:
            steps = [("Open", 9), ("Check", 10), ("Close", 12)]
        elif c < 8:
            steps = [("Open", 9), ("Check", 10), ("Fix", 13), ("Check", 15), ("Close", 16)]
        else:
            steps = [("Open", 9), ("Close", 10)]
        for a, h in steps:
            rows.append(f"c{c},{a},2024-01-01T{h:02d}:00:00Z")
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="a", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity",
                                        "timestamp_column": "time"})["log_id"]
    r = ap.activity_profile(log, "Check")
    assert r["cases_with_activity"] == 8 and r["share_of_cases"] == 0.8 and r["occurrences"] == 10
    assert r["cases_repeating_it"] == 2
    assert r["comes_after"][0] == {"activity": "Open", "times": 8, "share": 0.8, "median_hours": 1}
    assert {n["activity"] for n in r["comes_before"]} == {"Close", "Fix"}
    assert r["case_hours"]["with_activity"]["median"] == 3 and r["case_hours"]["without_activity"]["median"] == 1
    assert r["by_weekday"][0] == {"day": "Mon", "times": 10} and r["by_hour"][10]["times"] == 8
    with pytest.raises(EventLogWorkspaceError, match="no case"):
        ap.activity_profile(log, "Nope")


def test_literal_start_and_end_labels_are_separate_from_boundaries():
    rows = [
        "case,activity,time",
        "first,Check,2024-01-01T00:00:00Z",
        "first,(end),2024-01-01T02:00:00Z",
        "last,(start),2024-01-02T00:00:00Z",
        "last,Check,2024-01-02T01:00:00Z",
    ]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="boundary labels", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity",
                                        "timestamp_column": "time"})["log_id"]
    report = ap.activity_profile(log, "Check")
    for key, label, hours in (("comes_after", "(start)", 1), ("comes_before", "(end)", 2)):
        rows = report[key]
        actual = [row for row in rows if not row.get("boundary")]
        boundary = [row for row in rows if row.get("boundary")]
        assert actual == [{"activity": label, "times": 1, "share": 0.5, "median_hours": hours}]
        assert boundary == [{"activity": label, "times": 1, "share": 0.5, "median_hours": None, "boundary": True}]
