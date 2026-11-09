"""Opt-in interaction detail (#634) never establishes reconstruction membership."""
from __future__ import annotations

from evals.reconstruction import interaction_detail
from evals.reconstruction.fixtures import load_cases
from evals.reconstruction.prepare import split_cases
from server.capture_depth import HIGH_FIDELITY_EVENT_TYPES
from server.interaction_detail_events import INTERACTION_DETAIL_EVENT_TYPES, is_interaction_detail
from server.record_case_lanes import record_case_lanes
from server.work_item_links import link_observations

REF = "owg:r:" + "a" * 32
OTHER = "owg:r:" + "b" * 32


def _event(event_id, at, *, ref=None, event_type="browser_click", tab="t-1"):
    meta = {"page": {"hostname": "crm.example", "pathname": "/opaque"}, "tab_context_id": tab}
    if ref:
        meta["resource_reference"] = {"resource_ref": ref, "provider": "salesforce",
                                      "resource_kind": "case", "host": "crm.example"}
    return {"event_id": event_id, "observed_at": f"2026-10-06T08:00:{at:02d}Z", "event_type": event_type,
            "source": "browser_extension", "app": "Google Chrome", "actor_id": "human",
            "session_id": "s", "organization_id": "local", "metadata": meta}


def test_detail_types_match_capture_depth():
    assert INTERACTION_DETAIL_EVENT_TYPES == frozenset(HIGH_FIDELITY_EVENT_TYPES)
    assert not is_interaction_detail({"event_type": {"nested": 1}})
    assert not is_interaction_detail(None)


def test_a_hover_is_never_the_second_observation_of_a_record_lane():
    lone = [_event("e1", 1, ref=REF),
            _event("e1-hover", 0, ref=REF, event_type="browser_control_hover_dwell")]
    assert record_case_lanes(lone)["lanes"] == []
    pair = lone + [_event("e2", 5, ref=REF)]
    [lane] = record_case_lanes(pair)["lanes"]
    assert lane["observed_event_count"] == 3


def test_detail_joins_only_an_item_its_record_already_established():
    events = [_event("e1", 1, ref=REF), _event("e2", 5, ref=REF),
              _event("e1-hover", 0, ref=REF, event_type="browser_control_hover_dwell"),
              _event("x1", 10, ref=OTHER, tab="t-2"),
              _event("x1-hover", 9, ref=OTHER, tab="t-2", event_type="browser_control_hover_dwell"),
              _event("p1", 3, event_type="browser_pointer_trajectory")]
    linked = link_observations(events)
    items = sorted(sorted(ids) for ids in linked["members"].values() if len(ids) >= 2)
    assert items == [["e1", "e1-hover", "e2"]]
    assert linked["interaction_detail_events_attached"] == 1
    assert linked["interaction_detail_events_unassigned"] == 2
    assert linked["root"]("e1-hover") == linked["root"]("e1")
    assert linked["root"]("p1") == "p1"


def test_without_detail_the_linker_is_unchanged():
    events = [_event("e1", 1, ref=REF), _event("e2", 5, ref=REF), _event("x1", 10, ref=OTHER, tab="t-2")]
    linked = link_observations(events)
    assert "interaction_detail_events_attached" not in linked
    assert sorted(sorted(ids) for ids in linked["members"].values() if len(ids) >= 2) == [["e1", "e2"]]


def test_ablation_on_the_development_corpus():
    cases = split_cases(load_cases(), "development")[:12]
    report = interaction_detail.compare(cases)
    assert report["interaction_detail_events_added"] > 0
    for grouping, row in report["groupings"].items():
        assert row["ablation_reproduces_baseline"], grouping
        assert row["workflows_joined_only_by_interaction_detail"] == 0, grouping
        assert row["pointer_samples_assigned_to_a_workflow"] == 0, grouping
        # Detail can never add contamination or swallow an interruption.
        assert row["delta"]["cross_workflow_contamination"] <= 0, (grouping, row["delta"])
        assert row["delta"]["interruption_rejection"] >= 0, (grouping, row["delta"])
        assert row["delta"]["workflow_assignment_precision"] >= 0, (grouping, row["delta"])
    assert "Not evidence of improvement on real work" in report["interpretation"]
