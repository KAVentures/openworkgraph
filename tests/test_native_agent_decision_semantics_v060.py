from __future__ import annotations

from shared.codex_otel_adapter import codex_otel_to_agent_events


def _record(*, source: str) -> dict:
    return {
        "records": [{
            "attributes": {
                "event.name": "codex.tool_decision",
                "event.timestamp": "2026-09-25T01:30:00Z",
                "conversation.id": "decision-conv",
                "tool_name": "exec_command",
                "tool_namespace": "functions",
                "call_id": "decision-call",
                "decision": "denied",
                "source": source,
            }
        }]
    }


def test_codex_user_decision_is_human_approval_evidence():
    events, stats = codex_otel_to_agent_events(_record(source="user"))
    assert stats == {"records_seen": 1, "records_ignored": 0, "agent_events": 1}
    assert len(events) == 1
    assert events[0]["operation"] == "human_approval_received"
    assert events[0]["status"] == "denied"


def test_codex_automated_reviewer_decision_is_not_mislabeled_as_human():
    events, stats = codex_otel_to_agent_events(_record(source="automated_reviewer"))
    assert events == []
    assert stats == {"records_seen": 1, "records_ignored": 1, "agent_events": 0}


def test_codex_decision_without_source_is_not_assumed_human():
    events, stats = codex_otel_to_agent_events(_record(source=""))
    assert events == []
    assert stats == {"records_seen": 1, "records_ignored": 1, "agent_events": 0}
