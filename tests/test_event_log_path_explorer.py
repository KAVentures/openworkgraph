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
    evidence = px.branch_cases(log, ["Open", "Check"], None, selection={"activity": "Close"},
                               anchor="anywhere", anchor_steps=1)
    assert evidence["total"] == 1
    assert evidence["cases"] == [{"case_id": "c1", "events": 4}]


def test_literal_end_label_is_an_activity_distinct_from_case_end():
    log = _import([[("Open", 0)], [("Open", 0), ("(end)", 1), ("Close", 2)]])
    page = px.next_steps(log, ["Open"])
    assert [(row["activity"], row["is_end"], row["cases"]) for row in page["next"]] == [
        ("(end)", False, 1), ("(end)", True, 1)]
    assert page["next"][0]["hours_from_previous"]["median"] == 1
    assert page["next"][1]["hours_from_previous"] is None
    assert px.next_steps(log, ["Open", "(end)"])["next"][0]["activity"] == "Close"
    for selection, case_id, events, ends in [
        ({"activity": "(end)"}, "c1", 3, False),
        ({"ends_here": True}, "c0", 1, True),
    ]:
        evidence = px.branch_cases(log, ["Open"], None, selection=selection)
        assert evidence["is_end"] is ends
        assert evidence["total"] == 1 and evidence["next_after"] is None
        assert evidence["cases"] == [{"case_id": case_id, "events": events}]


def test_branch_pages_use_literal_case_key_order_and_the_same_cohort():
    log = _import(CASES)
    cohort = {"activity": "Approve"}
    total = px.next_steps(log, cohort=cohort)["next"][0]["cases"]
    first = px.branch_cases(log, [], None, selection={"activity": "Open", "limit": 2}, cohort=cohort)
    second = px.branch_cases(log, [], None,
                             selection={"activity": "Open", "limit": 2, "after": first["next_after"]},
                             cohort=cohort)
    last = px.branch_cases(log, [], None, selection={"activity": "Open", "after": second["next_after"]},
                           cohort=cohort)
    assert first["total"] == second["total"] == last["total"] == total == 6
    assert [case["case_id"] for page in (first, second, last) for case in page["cases"]] == [
        "c0", "c1", "c2", "c3", "c4", "c5"]
    assert last["next_after"] is None
    drafts = px.branch_cases(log, [], None, selection={"activity": "Draft", "limit": 1}, cohort=cohort)
    assert drafts["next_after"] == "c10"


@pytest.mark.parametrize("selection", [
    [], {}, {"unknown": 1}, {"activity": ""}, {"ends_here": False}, {"ends_here": 1},
    {"activity": "(end)", "ends_here": True}, {"activity": "Open", "after": ""},
    {"activity": "Open", "limit": True}, {"activity": "Open", "limit": 101},
])
def test_branch_rejects_invalid_settings(selection):
    with pytest.raises(EventLogWorkspaceError):
        px.branch_cases("unused", [], None, selection=selection)


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


def test_large_branch_count_keeps_exact_total_and_bounded_rows():
    rows = ['case,activity,time']
    for i in range(40):
        rows.extend([f'c{i},Start,2024-01-01T00:00:00Z',
                     f'c{i},Branch {i:02d},2024-01-01T01:00:00Z'])
    rows.append('last,Start,2024-01-01T00:00:00Z')
    log = workspace.import_log(BytesIO('\n'.join(rows).encode()), name='branches', source_format='csv',
        mapping={'case_column':'case', 'activity_column':'activity', 'timestamp_column':'time'})['log_id']
    result = px.next_steps(log, ['Start'])
    assert result['cases_at_point'] == 41
    assert result['next_total'] == 41
    assert len(result['next']) == px.MAX_NEXT
    assert result['next'][0]['activity'] == 'Branch 00'
    assert sum(row['cases'] for row in result['next']) == 30
    absent = px.next_steps(log, ['Missing'])
    assert absent['cases_at_point'] == 0 and absent['next_total'] == 0 and absent['next'] == []
