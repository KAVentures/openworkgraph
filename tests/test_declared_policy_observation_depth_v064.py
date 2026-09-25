from __future__ import annotations

import server.procedural_memory as pm
from server.declared_policy import compare_policy_to_observations


def _event(run: str, second: int, operation: str, *, level: str, status: str = "success") -> dict:
    return {
        "event_id": f"depth-{run}-{operation}-{second}",
        "observed_at": f"2026-09-25T10:00:{second:02d}+00:00",
        "source": "agent",
        "actor_id": "agent:depth-test",
        "session_id": f"session-{run}",
        "app": "Depth Test Agent",
        "event_type": f"agent_{operation}",
        "duration_seconds": 0,
        "metadata": {
            "operation": operation,
            "status": status,
            "observation_level": level,
            "agent": {"framework": "openai-agents-python"},
            "trace": {
                "run_id": run,
                "trace_id": f"trace-{run}",
                "workflow_id": "depth-workflow",
            },
            "tool": {"name": "", "category": "none"},
        },
    }


def _run(level: str) -> list[dict]:
    return [
        _event("instrumented", 0, "run_started", level=level, status="running"),
        _event("instrumented", 1, "model_call", level=level),
        _event("instrumented", 2, "run_finished", level=level),
    ]


def test_instrumented_tools_cannot_prove_missing_approval(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _run("instrumented_tools")
    family_key = pm.derive_executions(raw)[0]["family_key"]
    policy = {
        "family_key": family_key,
        "rules": [{
            "rule_id": "approval-required",
            "type": "required_step",
            "step": "approval_request",
        }],
    }

    comparison = compare_policy_to_observations(raw, policy=policy)
    counts = comparison["rule_results"][0]["result_counts"]
    assert counts["potential_divergence"] == 0
    assert counts["insufficient_observation"] == 1
    assert comparison["negative_evidence_coverage_rule"] == (
        "native_trace:any_structural_step; instrumented_tools:tool_steps_only"
    )


def test_instrumented_tools_can_support_absence_for_tool_step_rule(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _run("instrumented_tools")
    family_key = pm.derive_executions(raw)[0]["family_key"]
    policy = {
        "family_key": family_key,
        "rules": [{
            "rule_id": "search-required",
            "type": "required_step",
            "step": "tool:search:tool:0123456789ab",
        }],
    }

    comparison = compare_policy_to_observations(raw, policy=policy)
    counts = comparison["rule_results"][0]["result_counts"]
    assert counts["potential_divergence"] == 1
    assert counts["insufficient_observation"] == 0
