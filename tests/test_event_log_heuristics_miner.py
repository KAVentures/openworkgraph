"""Heuristics miner: dependency arrows, AND/XOR splits and joins, loops and noise tolerance."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_heuristics_miner as hm
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(traces):
    rows = ["case,activity,time"]
    for i, trace in enumerate(traces):
        for j, activity in enumerate(trace):
            rows.append(f"c{i},{activity},2024-01-01T{j // 60:02d}:{j % 60:02d}:{i % 60:02d}Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _edges(result):
    return {(e["from"], e["to"]): e for e in result["edges"]}


def test_parallel_and_choice_and_noise():
    # Register, then Check and Pay in either order (parallel), then Approve or Reject (choice), then Close.
    traces = []
    for i in range(40):
        middle = ["Check", "Pay"] if i % 2 else ["Pay", "Check"]
        traces.append(["Register", *middle, "Approve" if i % 3 else "Reject", "Close"])
    traces.append(["Register", "Close", "Check"])   # one noisy case
    result = hm.heuristics_net(_import(traces), min_occurrences=2)
    edges = _edges(result)
    assert ("Register", "Check") in edges and ("Register", "Pay") in edges
    assert ("Check", "Pay") not in edges and ("Pay", "Check") not in edges   # two-way evidence cancels out
    assert ("Close", "Check") not in edges   # seen once, below min_occurrences
    split = next(s for s in result["splits"] if s["activity"] == "Register")
    assert split["type"] == "and" and split["branches"] == ["Check", "Pay"]
    join = next(j for j in result["joins"] if j["activity"] == "Close")
    assert join["type"] == "xor" and join["branches"] == ["Approve", "Reject"]
    assert edges[("▶ start", "Register")]["transition_count"] == 41
    assert 0 < result["coverage"]["transitions_on_kept_arrows_share"] < 1
    assert result["cases"] == 41


def test_self_loops_and_short_loops():
    traces = [["A", "B", "B", "B", "C"]] * 10 + [["A", "D", "E", "D", "E", "D", "C"]] * 10
    log = _import(traces)
    result = hm.heuristics_net(log)
    kinds = {(lp["kind"], tuple(lp["activities"])) for lp in result["loops"]}
    assert ("self", ("B",)) in kinds and ("length_two", ("D", "E")) in kinds
    edges = _edges(result)
    assert ("B", "B") in edges and ("D", "E") in edges and ("E", "D") in edges
    short = next(loop for loop in result["loops"] if loop["kind"] == "length_two")
    picked = {"group": "length_two", "activities": short["activities"], "limit": 5}
    first = hm.evidence_cases(log, selection=picked)
    second = hm.evidence_cases(log, selection={**picked, "after": first["next_after"]})
    assert first["total"] == second["total"] == 10
    assert first["occurrences"] == second["occurrences"] == short["occurrences"] == 30
    assert all(c["occurrences"] == 3 for c in first["cases"] + second["cases"])
    assert second["next_after"] is None
    repeated = hm.evidence_cases(log, selection={"group": "transition", "activities": ["B", "B"]})
    assert repeated["occurrences"] == edges[("B", "B")]["transition_count"] == 20


def test_boundary_evidence_uses_exact_labels_and_the_case_filter():
    log = _import([[hm.START, "B", hm.END], ["X", "B", hm.END], [hm.START, "B", "Y"]])
    cohort = {"activity": hm.END}
    report = hm.heuristics_net(log, cohort=cohort)
    start = hm.evidence_cases(log, cohort=cohort, selection={"group": "start", "activities": [hm.START]})
    end = hm.evidence_cases(log, cohort=cohort, selection={"group": "end", "activities": [hm.END]})
    transition = hm.evidence_cases(log, cohort=cohort,
                                   selection={"group": "transition", "activities": [hm.START, "B"]})
    assert start["total"] == report["start_activities"][hm.START] == 1
    assert end["total"] == report["end_activities"][hm.END] == 2
    assert start["cases"] == transition["cases"]
    for bad in ({"group": "length_two", "activities": ["B", "B"]},
                {"group": "transition", "activities": ["X", "B"], "limit": True}):
        with pytest.raises(EventLogWorkspaceError):
            hm.evidence_cases(log, selection=bad)


def test_threshold_and_all_connected():
    traces = [["A", "B", "C"]] * 6 + [["A", "C", "B"]] * 4
    strict = hm.heuristics_net(_import(traces), dependency_threshold=0.9, all_connected=False)
    loose = hm.heuristics_net(_import(traces), dependency_threshold=0.9)
    # A ⇒ B is 6/7 ≈ 0.86, below 0.9: without all_connected nothing links the activities.
    assert not [e for e in strict["edges"] if e["dependency"] is not None]
    connected = _edges(loose)
    assert connected[("A", "B")]["kept_by"] == "best_connection" and connected[("A", "B")]["dependency"] == 0.8571
    assert ("B", "C") in connected and ("C", "B") in connected


def test_validation_and_activity_limit():
    log = _import([["A", "B"]] * 3)
    for kwargs in ({"dependency_threshold": 1.5}, {"and_threshold": -0.1}, {"min_occurrences": 0},
                   {"all_connected": "yes"}, {"dependency_threshold": True}):
        with pytest.raises(EventLogWorkspaceError):
            hm.heuristics_net(log, **kwargs)
    wide = _import([[f"X{i}" for i in range(70)]])
    with pytest.raises(EventLogWorkspaceError, match="at most 64"):
        hm.heuristics_net(wide)
