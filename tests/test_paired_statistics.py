"""Paired McNemar/bootstrap statistics for the three-arm evaluation (roadmap #264 Stage 7)."""
from __future__ import annotations

import pytest

from evals.paired_statistics import compare_arms, exact_sign_test, paired_bootstrap_interval
from evals.product_value import report


@pytest.mark.parametrize("gains,losses,expected", [
    (0, 0, 1.0),
    (10, 0, 2 / 1024),          # 2 * C(10,0) / 2**10
    (6, 1, 2 * (1 + 7) / 128),  # 2 * (C(7,0) + C(7,1)) / 2**7
    (5, 5, 1.0),                # symmetric discordance caps at 1
    (1, 6, 2 * (1 + 7) / 128),
])
def test_exact_sign_test_matches_hand_computed_binomial_tails(gains, losses, expected):
    assert exact_sign_test(gains, losses) == pytest.approx(expected)


def test_bootstrap_interval_is_deterministic_and_degenerate_when_differences_are_constant():
    assert paired_bootstrap_interval([False] * 8, [True] * 8) == (1.0, 1.0)
    assert paired_bootstrap_interval([True] * 8, [True] * 8) == (0.0, 0.0)
    mixed_b = [False, True] * 10
    mixed_t = [True, True] * 10
    first = paired_bootstrap_interval(mixed_b, mixed_t)
    assert first == paired_bootstrap_interval(mixed_b, mixed_t)
    assert 0.0 < first[0] <= 0.5 <= first[1] < 1.0
    with pytest.raises(ValueError):
        paired_bootstrap_interval([True], [True, False])


def _rows(successes, harms=None):
    harms = harms or [False] * len(successes)
    return [{"episode_id": f"e{i}", "continuation_success": s, "consequential_wrong_action": h}
            for i, (s, h) in enumerate(zip(successes, harms))]


def test_large_consistent_paired_gain_is_supported():
    control = _rows([False] * 15 + [True] * 15)
    goal = _rows([True] * 12 + [False] * 3 + [True] * 15)
    result = compare_arms(control, goal)
    assert result["continuation_success"]["treatment_only_true"] == 12
    assert result["continuation_success"]["baseline_only_true"] == 0
    assert result["continuation_success"]["exact_mcnemar_p_value"] < 0.001
    assert result["continuation_success"]["bootstrap_95_interval"][0] > 0
    assert result["success_gain_statistically_supported"] is True


def test_small_gain_or_added_harm_is_not_supported():
    control = _rows([False] * 30)
    small = _rows([True] * 3 + [False] * 27)
    assert compare_arms(control, small)["success_gain_statistically_supported"] is False
    harmful = _rows([True] * 12 + [False] * 18, harms=[True] + [False] * 29)
    result = compare_arms(control, harmful)
    assert result["consequential_wrong_action"]["treatment_only_true"] == 1
    assert result["success_gain_statistically_supported"] is False


def test_misaligned_arms_are_rejected():
    with pytest.raises(ValueError):
        compare_arms(_rows([True, False]), list(reversed(_rows([True, False]))))


def _three_arm(count, goal_wins):
    episodes = [{"episode_id": f"e{i}", "correct_resources": [f"f{i}"], "correct_agent_runs": [],
                 "real_observed_work": True, "ground_truth_verified_by": "reviewer"} for i in range(count)]

    def arm(name):
        return [{
            "episode_id": f"e{i}", "recorded_model_completion": True, "model_id": "m",
            "source_state_id": f"s{i}",
            "independent_review": {
                "reviewer_id": "r", "selected_resources": [f"f{i}"] if name != "control" else [],
                "selected_agent_runs": [], "clarification_questions": 0, "wrong_assumptions": 0,
                "continuation_success": name == "goal" and i < goal_wins,
                "consequential_wrong_action": False,
            },
        } for i in range(count)]
    return episodes, arm("control"), arm("raw"), arm("goal")


def test_report_adds_paired_statistics_without_changing_the_existing_decision():
    result = report(*_three_arm(24, goal_wins=14))
    assert result["status"] == "reviewed_real_episode_comparison"
    assert result["product_decision"] == "continue_continuity_thesis"
    stats = result["paired_statistics"]
    assert set(stats) == {"goal_vs_no_owg", "raw_vs_no_owg", "goal_vs_raw"}
    assert stats["goal_vs_no_owg"]["continuation_success"]["treatment_only_true"] == 14
    assert stats["goal_vs_no_owg"]["success_gain_statistically_supported"] is True
    assert stats["raw_vs_no_owg"]["continuation_success"]["rate_difference"] == 0
    assert stats["raw_vs_no_owg"]["success_gain_statistically_supported"] is False
