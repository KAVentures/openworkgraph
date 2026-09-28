from __future__ import annotations

import json

from shared.agent_evidence import agent_event_to_evidence
from shared.gemini_otel_adapter import gemini_otel_to_agent_events

SECRET = "DO-NOT-STORE-THIS"


def _kv(key, value):
    if isinstance(value, bool):
        wrapped = {"boolValue": value}
    elif isinstance(value, int):
        wrapped = {"intValue": str(value)}
    else:
        wrapped = {"stringValue": str(value)}
    return {"key": key, "value": wrapped}


def _record(stamp: int, **attrs):
    return {
        "timeUnixNano": str(1_790_000_000_000_000_000 + stamp),
        "attributes": [_kv(k, v) for k, v in attrs.items()],
    }


def test_gemini_session_and_explicit_agent_runs_get_finish_boundaries_without_content():
    payload = {
        "resourceLogs": [{
            "resource": {"attributes": [_kv("session.id", "session-1")]},
            "scopeLogs": [{"logRecords": [
                _record(1, **{"event.name": "gemini_cli.user_prompt", "prompt_id": "prompt-1", "prompt": SECRET}),
                _record(2, **{"event.name": "gemini_cli.agent.start", "agent_id": "agent-1", "agent_name": SECRET}),
                _record(3, **{
                    "event.name": "gemini_cli.agent.finish",
                    "agent_id": "agent-1",
                    "agent_name": SECRET,
                    "duration_ms": 2500,
                    "terminate_reason": "cancelled_by_user",
                    "routing.error_message": SECRET,
                }),
                _record(4, **{"event.name": "gemini_cli.conversation_finished", "turnCount": 1}),
            ]}],
        }],
    }

    events, stats = gemini_otel_to_agent_events(payload)
    assert stats == {"records_seen": 4, "records_ignored": 0, "agent_events": 4}
    assert [(e["operation"], e["status"], e["run_id"]) for e in events] == [
        ("run_started", "running", "prompt-1"),
        ("run_started", "running", "agent-1"),
        ("run_finished", "cancelled", "agent-1"),
        ("run_finished", "unknown", "session-1"),
    ]
    assert events[2]["duration_seconds"] == 2.5
    assert events[3]["session_id"] == "session-1" and events[3]["run_id"] == events[3]["session_id"]
    assert SECRET not in json.dumps(events)
    for event in events:
        agent_event_to_evidence(event)


def test_gemini_does_not_invent_a_turn_finish_from_api_response():
    payload = {
        "resourceLogs": [{
            "resource": {"attributes": [_kv("session.id", "session-2")]},
            "scopeLogs": [{"logRecords": [
                _record(1, **{"event.name": "gemini_cli.user_prompt", "prompt_id": "prompt-2"}),
                _record(2, **{"event.name": "gemini_cli.api_response", "prompt_id": "prompt-2", "status_code": 200}),
            ]}],
        }],
    }
    events, _ = gemini_otel_to_agent_events(payload)
    assert [e["operation"] for e in events] == ["run_started", "model_call"]
    assert not any(e["operation"] == "run_finished" for e in events)
