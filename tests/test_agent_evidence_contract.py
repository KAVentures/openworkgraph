from __future__ import annotations

import pytest

from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence


def _base(**overrides):
    payload = {
        "event_id": "agent-event-1",
        "observed_at": "2026-09-25T00:45:00+02:00",
        "organization_id": "acme",
        "agent_name": "Example Agent",
        "provider": "example-provider",
        "framework": "example-framework",
        "model": "example-model",
        "operation": "tool_call",
        "status": "success",
        "observation_level": "native_trace",
        "run_id": "run-1",
        "trace_id": "trace-1",
        "span_id": "span-2",
        "parent_span_id": "span-1",
        "workflow_id": "workflow-9",
        "tool_name": "repository_search",
        "tool_category": "search",
        "duration_seconds": 1.25,
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }
    payload.update(overrides)
    return payload


def test_agent_event_maps_into_existing_canonical_evidence_shape():
    event = agent_event_to_evidence(_base())

    assert set(event) == {
        "event_id",
        "observed_at",
        "schema_version",
        "organization_id",
        "actor_id",
        "device_id",
        "sensor_id",
        "source",
        "session_id",
        "app",
        "window_title",
        "event_type",
        "duration_seconds",
        "screenshot_path",
        "metadata",
    }
    assert event["source"] == "agent"
    assert event["actor_id"] == "agent:example-agent"
    assert event["session_id"] == "run-1"
    assert event["event_type"] == "agent_tool_call"
    assert event["metadata"]["actor_kind"] == "agent"
    assert event["metadata"]["trace"] == {
        "run_id": "run-1",
        "trace_id": "trace-1",
        "span_id": "span-2",
        "parent_span_id": "span-1",
        "workflow_id": "workflow-9",
    }
    assert event["metadata"]["tool"] == {
        "name": "repository_search",
        "category": "search",
    }


def test_agent_event_privacy_flags_are_fail_closed_for_content():
    event = agent_event_to_evidence(_base())
    privacy = event["metadata"]["privacy"]

    assert privacy["prompt_content_captured"] is False
    assert privacy["model_response_content_captured"] is False
    assert privacy["tool_arguments_captured"] is False
    assert privacy["tool_result_content_captured"] is False
    assert privacy["chain_of_thought_captured"] is False
    assert privacy["raw_native_payload_captured"] is False
    assert event["window_title"] is None
    assert event["screenshot_path"] is None


@pytest.mark.parametrize(
    "payload",
    [
        _base(prompt="secret customer prompt"),
        _base(tool_result="raw tool output"),
        _base(extra={"response": "native provider response"}),
        _base(extra={"nested": {"chain_of_thought": "private reasoning"}}),
    ],
)
def test_content_bearing_fields_are_rejected_recursively(payload):
    with pytest.raises(AgentEvidenceError, match="content-bearing field"):
        agent_event_to_evidence(payload)


def test_agent_event_rejects_unknown_execution_semantics():
    with pytest.raises(AgentEvidenceError, match="invalid operation"):
        agent_event_to_evidence(_base(operation="click_everything"))
    with pytest.raises(AgentEvidenceError, match="invalid observation_level"):
        agent_event_to_evidence(_base(observation_level="omniscient"))
    with pytest.raises(AgentEvidenceError, match="invalid tool_category"):
        agent_event_to_evidence(_base(tool_category="mystery_tool"))


def test_session_can_fall_back_to_trace_and_explicit_actor_is_preserved():
    event = agent_event_to_evidence(
        _base(
            session_id="",
            run_id="",
            trace_id="trace-only",
            actor_id="agent:internal-researcher",
            tool_category="none",
            tool_name="",
            operation="run_started",
            status="running",
        )
    )
    assert event["session_id"] == "trace-only"
    assert event["actor_id"] == "agent:internal-researcher"
    assert event["event_type"] == "agent_run_started"
