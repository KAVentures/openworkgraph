from __future__ import annotations

import json

import pytest

from shared.agent_evidence import agent_event_to_evidence
from shared.codex_otel_adapter import codex_otel_to_agent_events


def _attr(key: str, value):
    if isinstance(value, bool):
        wrapped = {"boolValue": value}
    elif isinstance(value, int):
        wrapped = {"intValue": str(value)}
    else:
        wrapped = {"stringValue": str(value)}
    return {"key": key, "value": wrapped}


def _record(event_name: str, *, timestamp: str, conversation: str = "conv-1", **attrs):
    all_attrs = {
        "event.name": event_name,
        "event.timestamp": timestamp,
        "conversation.id": conversation,
        "model": "gpt-test",
        **attrs,
    }
    return {
        "timeUnixNano": "1790294400000000000",
        "body": {"stringValue": "BODY MUST NEVER BE STORED SUPERSECRET"},
        "attributes": [_attr(k, v) for k, v in all_attrs.items()],
    }


def _logs(records):
    return {"resourceLogs": [{"scopeLogs": [{"logRecords": records}]}]}


def test_codex_logs_map_structural_events_and_drop_sensitive_content():
    records = [
        _record(
            "codex.conversation_starts",
            timestamp="2026-09-25T00:00:00.000Z",
            **{
                "provider_name": "openai",
                "user.email": "private@example.com",
                "user.account_id": "acct-secret",
                "mcp_servers": "secret-mcp",
            },
        ),
        _record(
            "codex.tool_result",
            timestamp="2026-09-25T00:00:01.000Z",
            **{
                "tool_result_seq": 1,
                "tool_name": "exec_command",
                "tool_namespace": "functions",
                "call_id": "call-1",
                "duration_ms": 250,
                "success": True,
                "arguments": "cat /private/patient.txt SUPERSECRET",
                "output": "private@example.com SUPERSECRET",
                "mcp_server": "secret-server",
            },
        ),
        _record(
            "codex.tool_decision",
            timestamp="2026-09-25T00:00:01.500Z",
            **{
                "tool_name": "exec_command",
                "tool_namespace": "functions",
                "call_id": "call-2",
                "decision": "denied",
                "source": "user",
                "user.email": "private@example.com",
            },
        ),
        _record(
            "codex.api_request",
            timestamp="2026-09-25T00:00:02.000Z",
            **{
                "attempt": 1,
                "duration_ms": 700,
                "http.response.status_code": 200,
                "error.message": "",
                "auth.request_id": "private-request-id",
                "auth.agent_id": "private-agent-id",
            },
        ),
        _record(
            "codex.user_prompt",
            timestamp="2026-09-25T00:00:02.500Z",
            **{"prompt": "SUPERSECRET PROMPT"},
        ),
    ]
    events, stats = codex_otel_to_agent_events(_logs(records))
    assert stats == {"records_seen": 5, "records_ignored": 1, "agent_events": 4}
    assert [event["operation"] for event in events] == [
        "run_started",
        "tool_call",
        "human_approval_received",
        "model_call",
    ]
    assert events[1]["tool_name"] == "exec_command"
    assert events[1]["tool_category"] == "shell"
    assert events[1]["status"] == "success"
    assert events[1]["duration_seconds"] == 0.25
    assert events[2]["status"] == "denied"
    assert events[3]["status"] == "success"

    canonical = [agent_event_to_evidence(event) for event in events]
    serialized = json.dumps(canonical, ensure_ascii=False)
    for forbidden in (
        "SUPERSECRET",
        "private@example.com",
        "acct-secret",
        "private-request-id",
        "private-agent-id",
        "secret-server",
        "secret-mcp",
        "cat /private",
        "BODY MUST NEVER",
    ):
        assert forbidden not in serialized


def test_codex_trace_event_and_log_copy_dedupe_to_one_tool_event():
    common = {
        "event.name": "codex.tool_result",
        "event.timestamp": "2026-09-25T00:10:00.000Z",
        "conversation.id": "conv-dedupe",
        "tool_result_seq": "9",
        "tool_name": "read_file",
        "tool_namespace": "functions",
        "call_id": "call-9",
        "duration_ms": "12",
        "success": "true",
    }
    payload = {
        "resourceLogs": [{
            "scopeLogs": [{"logRecords": [{
                "attributes": [_attr(k, v) for k, v in common.items()],
                "body": {"stringValue": "secret log body"},
            }]}],
        }],
        "resourceSpans": [{
            "scopeSpans": [{"spans": [{
                "traceId": "otel-trace-do-not-use-as-conversation",
                "spanId": "root-span",
                "events": [{
                    "timeUnixNano": "1790295000000000000",
                    "attributes": [_attr(k, v) for k, v in common.items()],
                }],
            }]}],
        }],
    }
    events, stats = codex_otel_to_agent_events(payload)
    assert stats["records_seen"] == 2
    assert stats["agent_events"] == 1
    assert len(events) == 1
    assert events[0]["run_id"] == "conv-dedupe"
    assert events[0]["tool_category"] == "filesystem"


def test_codex_api_request_failure_uses_only_presence_not_error_text():
    payload = _logs([_record(
        "codex.api_request",
        timestamp="2026-09-25T00:20:00.000Z",
        **{
            "attempt": 2,
            "http.response.status_code": 500,
            "error.message": "patient@example.com SUPERSECRET backend details",
            "duration_ms": 123,
        },
    )])
    [event], _ = codex_otel_to_agent_events(payload)
    assert event["status"] == "error"
    assert event["duration_seconds"] == 0.123
    assert "SUPERSECRET" not in json.dumps(event)
    assert "patient@example.com" not in json.dumps(event)


def test_codex_missing_conversation_and_unknown_events_are_ignored():
    no_conversation = _record(
        "codex.tool_result",
        timestamp="2026-09-25T00:30:00.000Z",
        conversation="",
        **{"tool_name": "read_file", "call_id": "x", "success": True},
    )
    unknown = _record("codex.sse_event", timestamp="2026-09-25T00:30:01.000Z")
    events, stats = codex_otel_to_agent_events(_logs([no_conversation, unknown]))
    assert events == []
    assert stats == {"records_seen": 2, "records_ignored": 2, "agent_events": 0}


def test_codex_tool_names_are_sanitized_before_canonical_storage():
    payload = _logs([_record(
        "codex.tool_result",
        timestamp="2026-09-25T00:40:00.000Z",
        **{
            "tool_result_seq": 1,
            "tool_name": "read_file\npatient@example.com\t",
            "call_id": "call-x",
            "success": True,
        },
    )])
    [event], _ = codex_otel_to_agent_events(payload)
    assert "\n" not in event["tool_name"]
    assert "@" not in event["tool_name"]


def test_codex_record_limit_fails_closed():
    records = [
        _record("codex.api_request", timestamp=f"2026-09-25T00:50:{i:02d}.000Z", **{"attempt": i})
        for i in range(3)
    ]
    with pytest.raises(ValueError, match="exceeds 2 records"):
        codex_otel_to_agent_events(_logs(records), max_records=2)
