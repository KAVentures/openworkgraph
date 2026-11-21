"""Path explorer: next steps after a sequence."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_path_explorer as px
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(cases):
    rows = ["case,activity,time"]
    for i, steps in enumerate(cases):
        for activity, hour in steps:
            rows.append(f"c{i},{activity},2024-01-{1 + i % 28:02d}T{hour:02d}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="p", source_format="csv",
                                mapping=MAPPING)["log_id"]


CASES = ([[("Open", 0), ("Check", 1), ("Approve", 3), ("Close", 4)]] * 6
         + [[("Open", 0), ("Check", 2), ("Reject", 5)]] * 3
         + [[("Open", 0), ("Close", 1)]]
         + [[("Draft", 0), ("Open", 1), ("Check", 2), ("Approve", 6)]] * 2)


def test_from_the_start_and_one_level_down():
    log = _import(CASES)
    top = px.next_steps(log)
    assert top["cases_at_point"] == 12
    assert [(s["activity"], s["cases"]) for s in top["next"]] == [("Open", 10), ("Draft", 2)]
    after_open = px.next_steps(log, ["Open"])
    assert after_open["cases_at_point"] == 10
    assert [(s["activity"], s["cases"]) for s in after_open["next"]] == [("Check", 9), ("Close", 1)]
    check = after_open["next"][0]
    assert check["hours_from_previous"]["median"] == 1 and check["share"] == 0.9
    deeper = px.next_steps(log, ["Open", "Check"])
    assert [(s["activity"], s["cases"]) for s in deeper["next"]] == [("Approve", 6), ("Reject", 3)]
    assert deeper["hours_to_reach_point"]["median"] == 1
    ends = px.next_steps(log, ["Open", "Close"])
    assert ends["next"] == [{"activity": "(end)", "is_end": True, "cases": 1, "share": 1.0,
                             "hours_from_previous": None, "hours_to_case_end": None}]


def test_anywhere_finds_the_sequence_inside_cases():
    log = _import(CASES)
    r = px.next_steps(log, ["Open", "Check"], anchor="anywhere")
    assert r["cases_at_point"] == 11 and r["hours_to_reach_point"] is None
    assert {s["activity"]: s["cases"] for s in r["next"]} == {"Approve": 8, "Reject": 3}
    approve = next(s for s in r["next"] if s["activity"] == "Approve")
    assert approve["hours_from_previous"]["median"] == 2


def test_anywhere_branch_keeps_the_first_occurrence_of_the_anchor():
    log = _import([
        [("Open", 0), ("Hold", 1), ("Open", 2), ("Check", 3)],
        [("Draft", 0), ("Open", 1), ("Check", 2), ("Close", 3)],
        [("Open", 0), ("Check", 1), ("Hold", 2), ("Open", 3), ("Check", 4), ("Close", 5)],
    ])
    parent = px.next_steps(log, ["Open"], anchor="anywhere")
    assert {row["activity"]: row["cases"] for row in parent["next"]} == {"Check": 2, "Hold": 1}
    assert px.next_steps(log, ["Open", "Check"], anchor="anywhere")["cases_at_point"] == 3
    anchored = px.next_steps(log, ["Open", "Check"], anchor="anywhere", anchor_steps=1)
    assert anchored["cases_at_point"] == 2 and anchored["anchor_steps"] == 1
    assert {row["activity"]: row["cases"] for row in anchored["next"]} == {"Close": 1, "Hold": 1}
    assert px.next_steps(log, ["Open", "Check", "Close"], anchor="anywhere", anchor_steps=2)["cases_at_point"] == 1


def test_literal_end_label_is_an_activity_distinct_from_case_end():
    log = _import([[("Open", 0)], [("Open", 0), ("(end)", 1), ("Close", 2)]])
    page = px.next_steps(log, ["Open"])
    assert [(row["activity"], row["is_end"], row["cases"]) for row in page["next"]] == [
        ("(end)", False, 1), ("(end)", True, 1)]
    assert page["next"][0]["hours_from_previous"]["median"] == 1
    assert page["next"][1]["hours_from_previous"] is None
    assert px.next_steps(log, ["Open", "(end)"])["next"][0]["activity"] == "Close"


def test_bad_requests():
    log = _import(CASES)
    with pytest.raises(EventLogWorkspaceError, match="anchor"):
        px.next_steps(log, ["Open"], anchor="middle")
    with pytest.raises(EventLogWorkspaceError, match="at least one step"):
        px.next_steps(log, [], anchor="anywhere")
    with pytest.raises(EventLogWorkspaceError, match="at most"):
        px.next_steps(log, ["Open"] * 31)
    for bad in (0, True, 2):
        with pytest.raises(EventLogWorkspaceError, match="anchor_steps"):
            px.next_steps(log, ["Open"], anchor="anywhere", anchor_steps=bad)
    with pytest.raises(EventLogWorkspaceError, match="anchor_steps"):
        px.next_steps(log, ["Open"], anchor_steps=1)
    assert px.next_steps(log, ["Nope"])["cases_at_point"] == 0
