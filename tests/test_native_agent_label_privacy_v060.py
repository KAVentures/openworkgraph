from __future__ import annotations

import json

from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.codex_otel_adapter import codex_otel_to_agent_events


def _attr(key: str, value: str):
    return {"key": key, "value": {"stringValue": value}}


def test_claude_rejects_content_bearing_tool_and_agent_labels():
    [tool_event] = claude_hook_to_agent_events(
        {
            "session_id": "s1",
            "hook_event_name": "PostToolUse",
            "tool_use_id": "t1",
            "tool_name": "read_file patient@example.com",
        },
        observed_at="2026-09-25T00:00:00Z",
    )
    assert tool_event["tool_name"] == "unknown-tool"
    assert "patient@example.com" not in json.dumps(tool_event)

    [agent_event] = claude_hook_to_agent_events(
        {
            "session_id": "s1",
            "hook_event_name": "SubagentStart",
            "agent_id": "a1",
            "agent_type": "reviewer patient@example.com",
        },
        observed_at="2026-09-25T00:00:00Z",
    )
    assert agent_event["agent_name"] == "Claude Code/subagent"
    assert "patient@example.com" not in json.dumps(agent_event)


def test_codex_rejects_content_bearing_tool_and_model_labels():
    payload = {
        "records": [{
            "attributes": [
                _attr("event.name", "codex.tool_result"),
                _attr("event.timestamp", "2026-09-25T00:00:00Z"),
                _attr("conversation.id", "conv"),
                _attr("tool_result_seq", "1"),
                _attr("tool_name", "read_file patient@example.com"),
                _attr("call_id", "call-1"),
                _attr("success", "true"),
                _attr("model", "gpt-test patient@example.com"),
            ]
        }]
    }
    [event], _ = codex_otel_to_agent_events(payload)
    assert event["tool_name"] == "unknown-tool"
    assert event["model"] == ""
    assert "patient@example.com" not in json.dumps(event)
