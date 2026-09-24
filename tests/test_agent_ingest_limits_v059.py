from __future__ import annotations

import json

import pytest

from server.agent_ingest import ingest_otel_payload
from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence
from shared.otel_agent_adapter import otel_payload_to_agent_events


def test_otel_size_limit_includes_openworkgraph_defaults():
    with pytest.raises(AgentEvidenceError, match="exceeds"):
        ingest_otel_payload(
            {"spans": []},
            defaults={"agent_name": "A" * 2_100_000},
        )


def test_arbitrary_otel_span_name_is_not_used_as_tool_identity_or_stored_content():
    secret_span_name = "execute_tool customer-jane@example.com-secret-case-123"
    payload = {
        "spans": [{
            "trace_id": "privacy-trace-v059",
            "span_id": "privacy-span-v059",
            "name": secret_span_name,
            "started_at": "2026-09-25T00:00:00Z",
            "ended_at": "2026-09-25T00:00:01Z",
            "attributes": {"gen_ai.operation.name": "execute_tool"},
        }],
    }
    projected, stats = otel_payload_to_agent_events(payload, defaults={"agent_name": "Privacy Agent"})
    assert stats["agent_events"] == 1
    assert projected[0]["tool_name"] == ""
    canonical = agent_event_to_evidence(projected[0])
    assert secret_span_name not in json.dumps(canonical, ensure_ascii=False)
