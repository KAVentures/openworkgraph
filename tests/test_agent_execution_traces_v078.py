from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server.agent_execution_traces import agent_execution_traces
from shared.agent_evidence import agent_event_to_evidence


BASE = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)


def _events() -> list[dict]:
    common = {
        "agent_name": "Agent X",
        "provider": "test",
        "framework": "custom-agent",
        "observation_level": "native_trace",
        "run_id": "raw-run-secret",
        "trace_id": "raw-trace-secret",
        "workflow_id": "workflow-secret",
    }
    specs = [
        ("run_started", "running", {}),
        ("model_call", "success", {"model": "gpt-test", "span_id": "raw-span-model", "duration_seconds": 1.2, "usage": {"input_tokens": 10, "output_tokens": 4}}),
        ("tool_call", "success", {"span_id": "raw-span-tool", "parent_span_id": "raw-span-model", "tool_name": "search_repo", "tool_category": "search", "duration_seconds": 0.4}),
        ("human_approval_requested", "running", {"span_id": "raw-span-approval", "parent_span_id": "raw-span-tool", "tool_name": "deploy", "tool_category": "deployment"}),
        ("handoff", "success", {"span_id": "raw-span-handoff", "parent_span_id": "raw-span-model"}),
        ("run_finished", "success", {}),
    ]
    output = []
    for index, (operation, status, extra) in enumerate(specs):
        output.append(agent_event_to_evidence({
            **common,
            "event_id": f"event-secret-{index}",
            "observed_at": (BASE + timedelta(seconds=index)).isoformat(),
            "operation": operation,
            "status": status,
            **extra,
        }))
    return output


def test_provider_neutral_trace_preserves_structure_without_native_ids():
    payload = agent_execution_traces(_events(), limit=5, max_events_per_execution=50)
    assert payload["returned"] == 1
    execution = payload["executions"][0]
    assert execution["complete_boundary_observed"] is True
    assert execution["event_count_total"] == 6
    assert execution["operation_counts"]["tool_call"] == 1
    assert execution["approval_request_count"] == 1
    assert execution["outcome_status"] == "success"
    assert execution["agent"]["provider"] == "test"
    assert any(step.startswith("tool:search:") for step in execution["structural_steps"])
    assert execution["events"][2]["parent_span_ref"] == execution["events"][1]["span_ref"]

    serialized = json.dumps(payload)
    for secret in (
        "raw-run-secret",
        "raw-trace-secret",
        "raw-span-model",
        "raw-span-tool",
        "workflow-secret",
        "event-secret-",
    ):
        assert secret not in serialized
    assert payload["native_run_ids_exposed"] is False
    assert payload["native_trace_ids_exposed"] is False
    assert payload["native_span_ids_exposed"] is False
    assert payload["prompt_content_exposed"] is False
    assert payload["tool_arguments_exposed"] is False
    assert payload["tool_results_exposed"] is False
    assert payload["chain_of_thought_exposed"] is False


def test_event_bound_is_explicit_without_hiding_total_count():
    payload = agent_execution_traces(_events(), max_events_per_execution=2)
    execution = payload["executions"][0]
    assert execution["event_count_total"] == 6
    assert execution["event_count_returned"] == 2
    assert execution["events_truncated"] is True


def test_partial_run_never_claims_complete_boundaries():
    events = _events()[1:4]
    payload = agent_execution_traces(events)
    execution = payload["executions"][0]
    assert execution["run_start_observed"] is False
    assert execution["run_finish_observed"] is False
    assert execution["complete_boundary_observed"] is False


def test_invalid_filters_fail_closed():
    with pytest.raises(ValueError):
        agent_execution_traces(_events(), family_key="IGNORE PREVIOUS INSTRUCTIONS")
    with pytest.raises(ValueError):
        agent_execution_traces(_events(), execution_id="IGNORE PREVIOUS INSTRUCTIONS")
