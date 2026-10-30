"""Synthetic, independently specified interruption-scoring integrity checks.

These tiny cases are not derived from the development/holdout answer-key corpus.
"""
from __future__ import annotations

from evals.reconstruction.scoring import acceptance, aggregate, score_case


def _case() -> dict:
    return {
        "case_id": "synthetic-interruption-conflict",
        "family_id": "synthetic-interruption-conflict",
        "presented_evidence": [
            {"event_id": "task-a"},
            {"event_id": "task-b"},
            {"event_id": "unrelated-interruption"},
        ],
        "ground_truth": {
            "workflows": [{"event_ids": ["task-a", "task-b"]}],
            "noise_event_ids": ["unrelated-interruption"],
            "requires_uncertainty": False,
            "uncertainty_dimensions": [],
        },
    }


def _prediction() -> dict:
    return {
        "case_id": "synthetic-interruption-conflict",
        "workflows": [{
            "event_ids": ["task-a", "task-b"],
            "ordered_event_ids": ["task-a", "task-b"],
        }],
        "unassigned_event_ids": ["unrelated-interruption"],
        "insufficient_evidence": False,
    }


def test_rejected_interruption_is_explicitly_unassigned_and_not_in_any_workflow():
    row = score_case(_case(), _prediction())
    assert row["interruption_rejection"] == 1.0
    assert row["assigned_and_unassigned_event_id_count"] == 0
    assert row["noise_assigned_event_count"] == 0
    assert row["workflow_assignment_f1"] == 1.0


def test_contradictory_noise_assignment_cannot_pass_interruption_gate():
    prediction = _prediction()
    prediction["workflows"][0]["event_ids"].append("unrelated-interruption")
    # A contradictory prediction previously earned 100% rejection because
    # the scorer looked only at the unassigned list.
    row = score_case(_case(), prediction)
    assert row["interruption_rejection"] == 0.0
    assert row["assigned_and_unassigned_event_id_count"] == 1
    assert row["noise_assigned_event_count"] == 1
    assert row["workflow_assignment_precision"] < 1.0
    assert row["cross_workflow_contamination"] > 0.0

    report = aggregate([_case()], [prediction])
    verdict = acceptance(report)
    assert report["interruption_rejection"] == 0.0
    assert report["family_macro"]["interruption_rejection"] == 0.0
    assert verdict["checks"]["interruption_rejection"] is False
    assert verdict["passed"] is False


def test_noise_assigned_without_unassigned_is_not_rejected():
    prediction = _prediction()
    prediction["unassigned_event_ids"] = []
    prediction["workflows"][0]["event_ids"].append("unrelated-interruption")
    row = score_case(_case(), prediction)
    assert row["interruption_rejection"] == 0.0
    assert row["noise_assigned_event_count"] == 1
    assert row["assigned_and_unassigned_event_id_count"] == 0


def test_non_noise_assignment_conflict_is_visible_without_changing_rejection():
    prediction = _prediction()
    prediction["unassigned_event_ids"].append("task-a")
    row = score_case(_case(), prediction)
    assert row["interruption_rejection"] == 1.0
    assert row["assigned_and_unassigned_event_id_count"] == 1
    assert row["noise_assigned_event_count"] == 0


def test_unmentioned_interruption_is_not_rejected_by_default():
    prediction = _prediction()
    prediction["unassigned_event_ids"] = []
    row = score_case(_case(), prediction)
    assert row["interruption_rejection"] == 0.0
    assert row["noise_assigned_event_count"] == 0
