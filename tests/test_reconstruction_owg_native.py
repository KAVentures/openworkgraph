from __future__ import annotations

"""Precision/recall floors for OWG's own exact-record grouping (roadmap #264 Stage 2).

These score the shipped ``record_case_lanes`` against the event-level answer key
of the interleaved reconstruction corpus, including its family-split holdout,
missing observations and deleted evidence.
"""

from collections import defaultdict

from evals.reconstruction.ablations import ablate_event
from evals.reconstruction.baselines import score_baselines
from evals.reconstruction.fixtures import load_cases
from evals.reconstruction.owg_native import _ref, predict_record_lanes, score_record_lanes
from evals.reconstruction.prepare import split_cases


def _macro(report: dict) -> dict:
    return report["family_macro"]


def test_record_lanes_meet_regression_floors_on_all_and_holdout_cases():
    cases = load_cases()
    overall = _macro(score_record_lanes(cases))
    holdout = _macro(score_record_lanes(split_cases(cases, "holdout")))
    # Floors sit just below measured values (0.671 / 0.021 and 0.503 / 0.037)
    # so a grouping regression fails here instead of in a later model study.
    assert overall["workflow_assignment_f1"] >= 0.65, overall
    assert overall["cross_workflow_contamination"] <= 0.03, overall
    assert holdout["workflow_assignment_f1"] >= 0.45, holdout
    assert holdout["cross_workflow_contamination"] <= 0.05, holdout
    # Lanes only publish explicitly referenced records, never interruptions.
    assert overall["interruption_rejection"] == 1.0
    assert holdout["interruption_rejection"] == 1.0


def test_record_lanes_are_more_precise_than_every_naive_baseline():
    cases = load_cases()
    owg = _macro(score_record_lanes(cases))
    for name, report in score_baselines(cases).items():
        baseline = _macro(report)
        assert owg["cross_workflow_contamination"] < baseline["cross_workflow_contamination"], name
        assert owg["workflow_assignment_f1"] > baseline["workflow_assignment_f1"], name


def test_contamination_only_comes_from_one_record_shared_by_several_true_workflows():
    # Interleaving must never merge different records by temporal proximity.
    for case in load_cases():
        truth = {
            event_id: index
            for index, workflow in enumerate(case["ground_truth"]["workflows"])
            for event_id in workflow["event_ids"]
        }
        refs = {str(e["event_id"]): _ref(e) for e in case["source_events"]}
        for lane in predict_record_lanes(case)["workflows"]:
            assert len({refs[event_id] for event_id in lane["event_ids"]}) == 1, case["case_id"]
            workflows = {truth.get(event_id) for event_id in lane["event_ids"]}
            if len(workflows) > 1:
                shared = defaultdict(set)
                for event_id, ref in refs.items():
                    if event_id in truth:
                        shared[ref].add(truth[event_id])
                assert len(shared[refs[lane["event_ids"][0]]]) > 1, case["case_id"]


def test_missing_resource_observations_fail_closed_instead_of_guessing():
    cases = [{
        **case,
        "presented_evidence": [ablate_event(e, "resource_references") for e in case["presented_evidence"]],
        "source_events": [ablate_event(e, "resource_references") for e in case["source_events"]],
    } for case in load_cases()]
    for case in cases:
        prediction = predict_record_lanes(case)
        assert prediction["workflows"] == []
        assert len(prediction["unassigned_event_ids"]) == len(case["presented_evidence"])
    assert _macro(score_record_lanes(cases))["cross_workflow_contamination"] == 0.0


def test_deleted_evidence_never_reappears_in_lanes():
    for case in load_cases():
        refs = sorted({_ref(e) for e in case["source_events"] if _ref(e)})
        if not refs:
            continue
        revoked = refs[0]
        deleted = {str(e["event_id"]) for e in case["source_events"] if _ref(e) == revoked}
        kept = [e for e in case["source_events"] if str(e["event_id"]) not in deleted]
        remaining = {**case, "source_events": kept,
                     "presented_evidence": [e for e in case["presented_evidence"] if str(e["event_id"]) not in deleted]}
        prediction = predict_record_lanes(remaining)
        published = {event_id for lane in prediction["workflows"] for event_id in lane["event_ids"]}
        assert not published & deleted, case["case_id"]
        assert not deleted & set(prediction["unassigned_event_ids"]), case["case_id"]


# Adapter-integrity checks: the production record-lane API intentionally
# returns no event IDs. These tests exercise the evaluator's private projection
# without changing the preregistered corpus, scorer, holdout or release floors.
def _lane_projection_event(event_id, ref, observed_at, *, provider="github"):
    return {
        "event_id": event_id,
        "observed_at": observed_at,
        "event_type": "browser_click",
        "organization_id": "tenant-a",
        "metadata": {"resource_reference": {
            "resource_ref": ref,
            "provider": provider,
            "resource_kind": "issue",
            "host": "github.com",
        }},
    }


def _lane_projection_case(source, presented=None):
    return {
        "case_id": "synthetic-lane-projection",
        "source_events": source,
        "presented_evidence": source if presented is None else presented,
    }


def test_lane_projection_excludes_invalid_timestamp_and_unpresented_source_rows():
    ref = "owg:r:" + "a" * 32
    valid_1 = _lane_projection_event("valid1", ref, "2026-10-20T10:00:00Z")
    invalid = _lane_projection_event("badtime", ref, "not-a-timestamp")
    valid_2 = _lane_projection_event("valid2", ref, "2026-10-20T10:01:00Z")
    source_only = _lane_projection_event("hidden", ref, "2026-10-20T10:02:00Z")
    result = predict_record_lanes(_lane_projection_case(
        [valid_1, invalid, valid_2, source_only],
        [valid_1, invalid, valid_2],
    ))
    assert [lane["event_ids"] for lane in result["workflows"]] == [["valid1", "valid2"]]
    assert result["unassigned_event_ids"] == ["badtime"]
    assert "hidden" not in str(result)


def test_lane_projection_uses_first_admitted_duplicate_not_earliest_duplicate():
    ref = "owg:r:" + "a" * 32
    first = _lane_projection_event("dup", ref, "2026-10-20T10:02:00Z")
    other = _lane_projection_event("other", ref, "2026-10-20T10:01:00Z")
    conflicting_later_row = _lane_projection_event("dup", ref, "2026-10-20T10:00:00Z")
    for source in ([first, other, conflicting_later_row],
                   [first, conflicting_later_row, other]):
        result = predict_record_lanes(_lane_projection_case(source))
        assert [lane["ordered_event_ids"] for lane in result["workflows"]] == [
            ["other", "dup"]
        ]
        assert result["unassigned_event_ids"] == []


def test_lane_projection_withholds_reused_event_id_across_distinct_source_records():
    a, b = "owg:r:" + "a" * 32, "owg:r:" + "b" * 32
    source = [
        _lane_projection_event("reused", a, "2026-10-20T10:00:00Z"),
        _lane_projection_event("a2", a, "2026-10-20T10:02:00Z"),
        _lane_projection_event("reused", b, "2026-10-20T10:01:00Z"),
        _lane_projection_event("b2", b, "2026-10-20T10:03:00Z"),
    ]
    for rows in (source, list(reversed(source))):
        result = predict_record_lanes(_lane_projection_case(rows))
        assert {tuple(lane["event_ids"]) for lane in result["workflows"]} == {
            ("a2",), ("b2",)
        }
        assert result["unassigned_event_ids"].count("reused") == 2
        assert sum("reused" in lane["event_ids"] for lane in result["workflows"]) == 0


def test_lane_projection_refuses_coerced_nonstring_event_id_but_preserves_valid_rows():
    ref = "owg:r:" + "a" * 32
    source = [
        _lane_projection_event(17, ref, "2026-10-20T10:00:00Z"),
        _lane_projection_event("valid1", ref, "2026-10-20T10:01:00Z"),
        _lane_projection_event("valid2", ref, "2026-10-20T10:02:00Z"),
    ]
    result = predict_record_lanes(_lane_projection_case(source))
    assert [lane["event_ids"] for lane in result["workflows"]] == [["valid1", "valid2"]]
    assert result["unassigned_event_ids"] == ["17"]


def test_lane_projection_preserves_offset_order_and_shipped_identity_conflict_veto():
    ref = "owg:r:" + "a" * 32
    first = _lane_projection_event("first", ref, "2026-10-20T11:00:00+01:00")
    second = _lane_projection_event("second", ref, "2026-10-20T10:01:00Z")
    result = predict_record_lanes(_lane_projection_case([second, first]))
    assert [lane["ordered_event_ids"] for lane in result["workflows"]] == [
        ["first", "second"]
    ]
    conflicting = _lane_projection_event("other-provider", ref,
                                          "2026-10-20T10:02:00Z", provider="jira")
    vetoed = predict_record_lanes(_lane_projection_case([first, second, conflicting]))
    assert vetoed["workflows"] == []
    assert set(vetoed["unassigned_event_ids"]) == {"first", "second", "other-provider"}
