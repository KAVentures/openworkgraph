from __future__ import annotations

import json

from adapters.openai_agents import OpenWorkGraphTracingProcessor
from server.agent_execution_traces import agent_execution_traces
from server.agent_observability import enrich_agent_execution_payload
from shared.agent_evidence import agent_event_to_evidence
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.claude_otel_adapter import claude_otel_to_agent_events
from shared.codex_otel_adapter import codex_otel_to_agent_events


def _claude_record(name: str, **attrs):
    return {"attributes": {"event.name": name, "session.id": "session-1", "prompt.id": "prompt-1", **attrs}}


def test_claude_otel_projects_rich_structure_and_never_content():
    payload = {
        "records": [
            _claude_record("claude_code.user_prompt", **{"prompt_length": 900, "prompt": "SUPERSECRET"}),
            _claude_record(
                "claude_code.api_request",
                model="claude-sonnet-5",
                input_tokens=120,
                output_tokens=30,
                cache_read_tokens=50,
                duration_ms=1250,
                request_id="private-request-id",
                response="SUPERSECRET response",
            ),
            _claude_record(
                "claude_code.tool_result",
                tool_name="Bash",
                tool_use_id="private-tool-id",
                success=True,
                duration_ms=400,
                tool_parameters="SUPERSECRET args",
                tool_result="patient@example.com",
            ),
            _claude_record(
                "claude_code.tool_decision",
                tool_name="Edit",
                tool_use_id="private-tool-approval",
                source="user_temporary",
                decision="accept",
            ),
            _claude_record(
                "claude_code.tool_decision",
                tool_name="Read",
                tool_use_id="automatic",
                source="config",
                decision="accept",
            ),
            _claude_record("claude_code.subagent_completed", agent_type="Explore", duration_ms=2000),
            _claude_record("claude_code.api_retries_exhausted", **{"error.message": "SUPERSECRET retry body"}),
        ]
    }
    events, stats = claude_otel_to_agent_events(payload)
    assert stats["records_seen"] == 7
    assert [event["operation"] for event in events] == [
        "run_started",
        "model_call",
        "tool_call",
        "human_approval_received",
        "handoff",
        "error",
    ]
    model = next(event for event in events if event["operation"] == "model_call")
    assert model["model"] == "claude-sonnet-5"
    assert model["usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cached_input_tokens": 50,
        "total_tokens": 150,
    }
    tool = next(event for event in events if event["operation"] == "tool_call")
    assert tool["tool_name"] == "Bash"
    assert tool["tool_category"] == "shell"
    assert all(event["run_id"] == "prompt-1" for event in events)
    assert all(event["sensor_id"] == "agent:claude-code-otel" for event in events)

    canonical = [agent_event_to_evidence(event) for event in events]
    serialized = json.dumps(canonical)
    for forbidden in (
        "SUPERSECRET",
        "patient@example.com",
        "private-request-id",
        "private-tool-id",
        "private-tool-approval",
        "tool_parameters",
    ):
        assert forbidden not in serialized


def test_claude_hooks_only_mark_model_and_tokens_not_observable():
    [tool] = claude_hook_to_agent_events(
        {
            "session_id": "session-1",
            "prompt_id": "prompt-1",
            "hook_event_name": "PostToolUse",
            "tool_use_id": "tool-1",
            "tool_name": "Read",
        },
        observed_at="2026-09-27T10:00:00Z",
    )
    raw = [agent_event_to_evidence(tool)]
    payload = enrich_agent_execution_payload(agent_execution_traces(raw), raw)
    [run] = payload["executions"]
    assert run["observed_operation_counts"]["tool_call"] == 1
    assert run["signal_capabilities"]["tool_call"]["status"] == "observable"
    assert run["signal_capabilities"]["model_call"]["status"] == "not_observable"
    assert run["signal_capabilities"]["token_usage"]["status"] == "not_observable"
    assert run["telemetry_depth"] == "hooks_only"


def test_claude_otel_plus_hooks_deduplicates_tool_count_and_exposes_rich_signals():
    [hook_tool] = claude_hook_to_agent_events(
        {
            "session_id": "session-1",
            "prompt_id": "prompt-1",
            "hook_event_name": "PostToolUse",
            "tool_use_id": "tool-1",
            "tool_name": "Read",
        },
        observed_at="2026-09-27T10:00:01Z",
    )
    otel, _ = claude_otel_to_agent_events({
        "records": [
            _claude_record("claude_code.user_prompt"),
            _claude_record(
                "claude_code.api_request",
                model="claude-sonnet-5",
                input_tokens=10,
                output_tokens=5,
                request_id="request-1",
            ),
            _claude_record(
                "claude_code.tool_result",
                tool_name="Read",
                tool_use_id="tool-1",
                success=True,
            ),
        ]
    })
    raw = [agent_event_to_evidence(event) for event in [*otel, hook_tool]]
    payload = enrich_agent_execution_payload(agent_execution_traces(raw), raw)
    [run] = payload["executions"]
    assert run["observed_operation_counts"]["tool_call"] == 1
    assert run["observed_operation_counts"]["model_call"] == 1
    assert run["usage_totals"]["total_tokens"] == 15
    assert run["models_observed"] == ["claude-sonnet-5"]
    assert run["signal_capabilities"]["model_call"]["status"] == "observable"
    assert run["signal_capabilities"]["human_approval_requested"]["status"] == "observable"
    assert run["telemetry_depth"] == "rich_native_events_plus_hooks"


def test_codex_turn_usage_and_spawn_handoff_are_structural():
    payload = {
        "records": [
            {
                "attributes": {
                    "event.name": "codex.api_request",
                    "conversation.id": "conversation-private",
                    "turn.id": "turn-7",
                    "model": "gpt-5.6-codex",
                    "attempt": 1,
                    "http.response.status_code": 200,
                    "gen_ai.usage.input_tokens": 80,
                    "gen_ai.usage.output_tokens": 20,
                    "codex.usage.total_tokens": 100,
                    "event.timestamp": "2026-09-27T10:00:00Z",
                    "prompt": "SUPERSECRET",
                }
            },
            {
                "attributes": {
                    "event.name": "codex.agent_communication",
                    "conversation.id": "conversation-private",
                    "turn.id": "turn-7",
                    "communication_id": "private-communication-id",
                    "kind": "spawn",
                    "state": "send",
                    "agents": "SUPERSECRET names",
                    "event.timestamp": "2026-09-27T10:00:01Z",
                }
            },
            {
                "attributes": {
                    "event.name": "codex.agent_communication",
                    "conversation.id": "conversation-private",
                    "turn.id": "turn-7",
                    "communication_id": "private-message-id",
                    "kind": "message",
                    "state": "send",
                    "message": "patient@example.com SUPERSECRET",
                }
            },
        ]
    }
    events, stats = codex_otel_to_agent_events(payload)
    assert stats["records_seen"] == 3
    assert [event["operation"] for event in events] == ["model_call", "handoff"]
    model = events[0]
    assert model["run_id"] == "turn-7"
    assert model["model"] == "gpt-5.6-codex"
    assert model["usage"]["total_tokens"] == 100
    assert events[1]["tool_name"] == "agent:spawn"
    assert all(event["sensor_id"] == "agent:codex-otel" for event in events)
    serialized = json.dumps([agent_event_to_evidence(event) for event in events])
    assert "SUPERSECRET" not in serialized
    assert "patient@example.com" not in serialized
    assert "private-communication-id" not in serialized


def test_codex_can_infer_semantic_event_from_structural_fields_when_event_name_is_callsite():
    events, _ = codex_otel_to_agent_events({
        "records": [{
            "attributes": {
                "event.name": "codex_otel::event_manager::log_event",
                "conversation.id": "conversation-1",
                "call_id": "private-call",
                "tool_name": "Bash",
                "success": True,
            }
        }]
    })
    assert len(events) == 1
    assert events[0]["operation"] == "tool_call"
    assert events[0]["tool_name"] == "Bash"


class _CaptureSink:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, event: dict) -> bool:
        self.events.append(dict(event))
        return True

    def force_flush(self, *, timeout: float = 2.0) -> bool:
        return True

    def shutdown(self, *, timeout: float = 2.0) -> None:
        return None


class _UsageObject:
    input_tokens = 31
    output_tokens = 9
    cached_input_tokens = 4
    total_tokens = 40
    secret_breakdown = "SUPERSECRET"


class _ResponseObject:
    model = "gpt-5.6"
    output = "SUPERSECRET response"


class _Data:
    type = "response"
    usage = _UsageObject()
    response = _ResponseObject()
    input = "patient@example.com SUPERSECRET"


class _Span:
    span_id = "span-private"
    trace_id = "trace-private"
    parent_id = None
    span_data = _Data()
    error = None
    started_at = "2026-09-27T10:00:00Z"
    ended_at = "2026-09-27T10:00:02Z"


def test_openai_agents_response_span_emits_model_usage_without_response_content():
    sink = _CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    span = _Span()
    processor.on_span_start(span)
    processor.on_span_end(span)
    [event] = sink.events
    assert event["operation"] == "model_call"
    assert event["sensor_id"] == "agent:openai-agents"
    assert event["model"] == "gpt-5.6"
    assert event["usage"] == {
        "input_tokens": 31,
        "output_tokens": 9,
        "cached_input_tokens": 4,
        "total_tokens": 40,
    }
    serialized = json.dumps(event)
    assert "SUPERSECRET" not in serialized
    assert "patient@example.com" not in serialized
