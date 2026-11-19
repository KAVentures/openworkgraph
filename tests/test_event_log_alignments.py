"""Optimal alignments of an event log on an imported PNML Petri net."""
from __future__ import annotations

import pytest

from server import event_log_alignments as al
from server import event_log_pnml as pnml
from server.event_log_workspace import EventLogWorkspaceError
from tests.test_event_log_petri_replay import _dirs, _import, _sequence_net  # noqa: F401  (fixture reuse)


def _moves(variant):
    return [(s["move"], s["log"] or s["model"]) for s in variant["alignment"]]


def test_discovered_net_aligns_every_case_at_zero_cost():
    traces = [["Register", "Check", "Pay", "Close"]] * 10 + [["Register", "Pay", "Check", "Close"]] * 6 \
        + [["Register", "Check", "Close"]] * 4
    log = _import(traces)
    xml, _ = pnml.discovered_pnml(log, noise=0.0)
    result = al.alignments(log, xml.encode())
    assert result["cases"] == 20 and result["fitness"]["log"] == 1.0
    assert result["fitness"]["fitting_cases"] == 20 and result["non_fitting_variants"] == []
    assert result["deviation_cost"]["total"] == 0 and result["unaligned"] == {"variants": 0, "cases": 0}


def test_skipped_extra_and_swapped_steps_are_named_exactly():
    log = _import([["A", "B", "C"]] * 6 + [["A", "C"]] * 3 + [["A", "B", "X", "C"]] * 2 + [["A", "C", "B"]])
    result = al.alignments(log, _sequence_net(["A", "B", "C"]))
    assert result["net"]["shortest_run_visible_steps"] == 3
    assert result["fitness"]["fitting_cases"] == 6
    by_path = {tuple(v["path"]): v for v in result["non_fitting_variants"]}
    skipped = by_path[("A", "C")]
    assert skipped["cost"] == 1 and skipped["fitness"] == round(1 - 1 / 5, 4)
    assert _moves(skipped) == [("sync", "A"), ("model", "B"), ("sync", "C")]
    extra = by_path[("A", "B", "X", "C")]
    assert _moves(extra) == [("sync", "A"), ("sync", "B"), ("log", "X"), ("sync", "C")]
    swapped = by_path[("A", "C", "B")]
    # Two explanations cost 2 (skip B then add B, or add C then skip C); one is returned.
    assert swapped["cost"] == 2 and sorted(m for m, _ in _moves(swapped)) == ["log", "model", "sync", "sync"]
    model_b = next(m for m in result["model_moves"] if m["activity"] == "B")
    assert model_b["cases"] >= 3
    assert result["log_moves"][0] == {"activity": "X", "cases": 2, "times": 2}
    assert result["activities_not_in_net"] == ["X"]
    total_cost = 3 * 1 + 2 * 1 + 1 * 2
    worst = 6 * 6 + 3 * 5 + 2 * 7 + 1 * 6
    assert result["deviation_cost"]["total"] == total_cost
    assert result["fitness"]["log"] == round(1 - total_cost / worst, 4)


def test_silent_transitions_are_free_and_hidden():
    log = _import([["A", "B", "C"]] * 5 + [["A", "C"]] * 5)
    result = al.alignments(log, _sequence_net(["A", "B", "C"], final=False, silent_skip=1))
    assert result["fitness"]["log"] == 1.0 and result["fitness"]["fitting_cases"] == 10
    # The shortest run takes the silent skip, so only A and C are visible steps.
    assert result["net"]["shortest_run_visible_steps"] == 2


def test_search_limit_reports_unaligned_variants(monkeypatch):
    log = _import([["A", "B", "C"]] * 3 + [["C", "B", "A", "A", "B"]])
    monkeypatch.setattr(al, "MAX_STATES_PER_VARIANT", 6)
    result = al.alignments(log, _sequence_net(["A", "B", "C"]))
    assert result["unaligned"]["variants"] >= 1
    assert {"path": ["C", "B", "A", "A", "B"], "cases": 1} in result["unaligned_variants"]
    assert result["cases"] + result["unaligned"]["cases"] == 4


def test_unreachable_final_marking_and_bad_files_are_refused():
    log = _import([["A", "B"]])
    dead = _sequence_net(["A", "B"]).replace(b'<arc id="a1o" source="t1" target="p2"/>', b"")
    with pytest.raises(EventLogWorkspaceError, match="cannot reach its final marking"):
        al.alignments(log, dead)
    with pytest.raises(EventLogWorkspaceError, match="DOCTYPE"):
        al.alignments(log, b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><pnml/>')


def test_fitness_by_case_start_month():
    from io import BytesIO
    from server import event_log_workspace as workspace
    rows = ["case,activity,time"]
    # January: every case fits; February: half skip B.
    for i in range(4):
        for j, a in enumerate(["A", "B", "C"]):
            rows.append(f"j{i},{a},2024-01-{10 + i:02d}T0{j}:00:00Z")
    for i in range(4):
        for j, a in enumerate(["A", "C"] if i % 2 else ["A", "B", "C"]):
            rows.append(f"f{i},{a},2024-02-{10 + i:02d}T0{j}:00:00Z")
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="m", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity",
                                        "timestamp_column": "time"})["log_id"]
    months = al.alignments(log, _sequence_net(["A", "B", "C"]))["by_month"]
    assert months == [{"month": "2024-01", "cases": 4, "fitting_case_share": 1.0, "fitness": 1.0},
                      {"month": "2024-02", "cases": 4, "fitting_case_share": 0.5,
                       "fitness": round(1 - 2 / (2 * 6 + 2 * 5), 4)}]
