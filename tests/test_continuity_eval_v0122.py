from __future__ import annotations


def test_continuity_scorer_penalizes_wrong_resources():
    from evals.continuity.scoring import aggregate

    episodes = [{"episode_id": "e1", "correct_resources": ["a", "b"], "correct_agent_runs": ["x"]}]
    result = aggregate(episodes, [{
        "episode_id": "e1",
        "selected_resources": ["a", "wrong"],
        "selected_agent_runs": ["x"],
        "clarification_questions": 1,
        "continuation_success": False,
    }])
    assert result["resource_precision"] == 0.5
    assert result["resource_recall"] == 0.5
    assert result["wrong_resource_rate"] == 0.5
    assert result["agent_run_recall"] == 1.0


def test_product_gate_requires_large_safe_gain():
    from evals.continuity.scoring import decision

    control = {
        "resource_recall": 0.55,
        "continuation_success_rate": 0.60,
        "clarification_questions_per_episode": 1.5,
        "wrong_resource_rate": 0.20,
        "wrong_assumptions_per_episode": 0.40,
        "consequential_wrong_action_rate": 0.05,
    }
    strong = {
        "resource_recall": 0.75,
        "continuation_success_rate": 0.72,
        "clarification_questions_per_episode": 1.0,
        "wrong_resource_rate": 0.15,
        "wrong_assumptions_per_episode": 0.30,
        "consequential_wrong_action_rate": 0.04,
    }
    weak = {
        "resource_recall": 0.59,
        "continuation_success_rate": 0.63,
        "clarification_questions_per_episode": 1.3,
        "wrong_resource_rate": 0.18,
        "wrong_assumptions_per_episode": 0.30,
        "consequential_wrong_action_rate": 0.04,
    }
    unsafe = {
        "resource_recall": 0.80,
        "continuation_success_rate": 0.80,
        "clarification_questions_per_episode": 0.8,
        "wrong_resource_rate": 0.25,
        "wrong_assumptions_per_episode": 0.30,
        "consequential_wrong_action_rate": 0.05,
    }
    assert decision(control, strong)["decision"] == "continue_continuity_thesis"
    assert decision(control, weak)["decision"] == "stop_or_pause_product_pivot"
    assert decision(control, unsafe)["decision"] == "stop_or_pause_product_pivot"


def test_product_gate_rejects_more_wrong_assumptions_even_with_higher_recall():
    from evals.continuity.scoring import decision

    control = {
        "resource_recall": 0.50,
        "continuation_success_rate": 0.50,
        "clarification_questions_per_episode": 1.5,
        "wrong_resource_rate": 0.10,
        "wrong_assumptions_per_episode": 0.20,
        "consequential_wrong_action_rate": 0.0,
    }
    owg = {
        "resource_recall": 0.80,
        "continuation_success_rate": 0.80,
        "clarification_questions_per_episode": 0.5,
        "wrong_resource_rate": 0.05,
        "wrong_assumptions_per_episode": 0.30,
        "consequential_wrong_action_rate": 0.0,
    }
    assert decision(control, owg)["decision"] == "stop_or_pause_product_pivot"
