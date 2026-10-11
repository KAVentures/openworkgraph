"""Tests for the primary reviewed three-arm evaluation; no model calls."""
from __future__ import annotations

from copy import deepcopy

import pytest

from evals.product_value import report


def _data(count: int = 2, *, verified_real: bool = False):
    episodes = [
        {
            "episode_id": f"e{i}",
            "correct_resources": [f"file{i}"],
            "correct_agent_runs": [],
            "real_observed_work": verified_real,
            "ground_truth_verified_by": "independent-evaluator" if verified_real else "",
        }
        for i in range(count)
    ]

    def arm(name: str):
        entries = []
        for i in range(count):
            selected = [f"file{i}"] if name != "control" else []
            entries.append({
                "episode_id": f"e{i}",
                "recorded_model_completion": True,
                "model_id": "fixture-model",
                "source_state_id": f"frozen-source-state-{i}",
                "independent_review": {
                    "reviewer_id": "second-evaluator",
                    "selected_resources": selected,
                    "selected_agent_runs": [],
                    "clarification_questions": 0,
                    "wrong_assumptions": 0,
                    "continuation_success": name == "goal",
                    "consequential_wrong_action": False,
                },
            })
        return entries
    return episodes, arm("control"), arm("raw"), arm("goal")


def test_three_arm_report_is_pilot_even_when_fixture_scores_are_perfect():
    cases, control, raw, goal = _data()
    result = report(cases, control, raw, goal)
    assert result["status"] == "pilot_only"
    assert result["product_decision"] == "insufficient_reviewed_real_episodes"
    assert result["episodes"] == 2
    assert result["model_calls_performed_by_this_script"] == 0
    assert result["metrics"]["control"]["resource_recall"] == 0
    assert result["metrics"]["raw"]["resource_recall"] == 1
    assert result["metrics"]["goal"]["continuation_success_rate"] == 1
    assert result["comparisons"]["goal_vs_no_owg"]["decision"] == "continue_continuity_thesis"


def test_rejects_incomplete_arms_duplicates_and_noncompleted_runs():
    cases, control, raw, goal = _data()
    with pytest.raises(ValueError, match="missing episodes"):
        report(cases, control, raw[:-1], goal)
    with pytest.raises(ValueError, match="duplicate episode_id"):
        report(cases, control + [control[0]], raw, goal)
    blocked = deepcopy(goal)
    blocked[0]["recorded_model_completion"] = False
    with pytest.raises(ValueError, match="only completed model turns"):
        report(cases, control, raw, blocked)


def test_rejects_unaudited_model_outcomes_or_unpaired_source_state():
    cases, control, raw, goal = _data()
    not_reviewed = deepcopy(goal)
    not_reviewed[0].pop("independent_review")
    with pytest.raises(ValueError, match="independent reviewer"):
        report(cases, control, raw, not_reviewed)
    inconsistent = deepcopy(raw)
    inconsistent[1]["model_id"] = "other-model"
    with pytest.raises(ValueError, match="model/source state mismatch"):
        report(cases, control, inconsistent, goal)
    inconsistent = deepcopy(goal)
    inconsistent[0]["source_state_id"] = "different-source-system-snapshot"
    with pytest.raises(ValueError, match="model/source state mismatch"):
        report(cases, control, raw, inconsistent)


def test_predefined_gate_does_not_reward_unsafe_goal_retrieval():
    cases, control, raw, goal = _data(20, verified_real=True)
    safe = report(cases, control, raw, goal)
    assert safe["status"] == "reviewed_real_episode_comparison"
    assert safe["product_decision"] == "continue_continuity_thesis"

    hazardous = deepcopy(goal)
    for row in hazardous:
        review = row["independent_review"]
        review["selected_resources"].append("wrong-account")
        review["wrong_assumptions"] = 1
    risky = report(cases, control, raw, hazardous)
    assert risky["product_decision"] == "stop_or_pause_product_pivot"
    assert risky["comparisons"]["goal_vs_raw"]["wrong_resource_rate_not_worse"] is False
