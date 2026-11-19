"""Footprints: log and model ordering relations and where they disagree."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_footprints as fp
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


def _cell(result, a, b):
    row = next(r for r in result["matrix"] if r["activity"] == a)
    return row["cells"][result["activities"].index(b)]


def test_tree_footprint_operators():
    leaf = lambda a: {"op": "activity", "activity": a}  # noqa: E731
    tree = {"op": "seq", "children": [
        leaf("A"),
        {"op": "and", "children": [leaf("B"), leaf("C")]},
        {"op": "xor", "children": [leaf("D"), {"op": "tau"}]},
        {"op": "loop", "children": [leaf("E"), {"op": "tau"}]},
    ]}
    f = fp._tree_fp(tree)
    assert {("A", "B"), ("A", "C"), ("B", "D"), ("C", "E"), ("D", "E"), ("E", "E")} <= set(f.seq)
    assert ("B", "C") in f.par and ("A", "D") not in f.seq
    assert f.start == {"A"} and f.end == {"E"} and not f.skip


def test_fitting_log_agrees_everywhere():
    traces = [["Register", "Check", "Pay", "Close"]] * 10 + [["Register", "Pay", "Check", "Close"]] * 10
    result = fp.footprints(_import(traces), noise=0.0)
    assert result["cases"] == 20 and result["activities"] == ["Check", "Close", "Pay", "Register"]
    assert result["conformance"]["footprint_fitness"] == 1.0 and result["differences_total"] == 0
    assert _cell(result, "Check", "Pay") == {"log": "||", "model": "||"}
    assert _cell(result, "Register", "Check") == {"log": "→", "model": "→"}
    assert _cell(result, "Close", "Register") == {"log": "#", "model": "#"}


def test_noise_filtered_ordering_shows_as_difference():
    traces = [["A", "B", "C"]] * 30 + [["A", "C", "B"]] * 2
    result = fp.footprints(_import(traces), noise=0.2)
    diffs = {(d["from"], d["to"]): d for d in result["differences"]}
    assert ("C", "B") in diffs and diffs[("C", "B")]["kind"] == "in_log_not_in_model"
    assert diffs[("C", "B")]["cases"] == 2
    assert result["conformance"]["footprint_fitness"] < 1
    assert "C" in result["end_activities"]["model"] or "B" in result["end_activities"]["log_only"]


def test_bad_input():
    log = _import([["A", "B"]] * 3)
    with pytest.raises(EventLogWorkspaceError):
        fp.footprints(log, noise=1.5)
