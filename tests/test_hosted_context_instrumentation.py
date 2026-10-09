"""Instrumented MCP trace recorder refuses to store observed private values."""
from __future__ import annotations

import json

from evals.hosted_context.instrumentation import MCPTraceRecorder


def test_recorder_logs_only_event_ids_and_structural_arguments(tmp_path):
    sensitive = "PRIVATE-SYNTHETIC-AGENT-PROMPT-DO-NOT-DISCLOSE"
    email = "synthetic@example.invalid"
    response = {
        "result": {"structuredContent": {"rows": [{
            "event_id": "evt-renewal-001",
            "observed_at": "2026-10-09T10:00:00Z",
            "event_type": "click",
            "window_title": email,
            "metadata": {
                "prompt": sensitive,
                "event_id": "FORGED-EVENT-ID",
            },
        }]}}
    }
    recorder = MCPTraceRecorder(case_id="named-renewal", client="test",
                                model="no-real-model", trial="1")
    returned = recorder.record(name="search_work", arguments={
        "query": email, "limit": 15, "cursor": sensitive,
        "authentication": "DONTLOG", "detail": "compact",
    }, response=response)
    assert returned is response
    path = tmp_path / "tool-traces.jsonl"
    recorder.append_jsonl(path)
    body = path.read_text()
    assert email not in body and sensitive not in body and "DONTLOG" not in body
    assert "FORGED-EVENT-ID" not in body
    data = json.loads(body)
    assert data["harness_recorded"] is True
    assert data["tool_calls"][0]["event_ids"] == ["evt-renewal-001"]
    assert data["tool_calls"][0]["arguments"]["query"] == "<present>"
    assert data["tool_calls"][0]["arguments"]["cursor"] == "<present>"
    assert data["tool_calls"][0]["response_chars"] > len(sensitive)


def test_recorded_unfiltered_fallback_is_distinguished(tmp_path):
    recorder = MCPTraceRecorder(case_id="raw-without-task", client="test",
                                model="no-real-model", trial="2")
    recorder.record(name="get_workflow_trace", arguments={
        "since": "2026-10-09T09:00:00Z",
        "limit": 25,
    }, response={"rows": [{
        "event_id": "evt-untagged-004",
        "observed_at": "2026-10-09T10:01:00Z",
        "event_type": "window_focus",
    }]})
    recorder.append_jsonl(tmp_path / "traces.jsonl")
    data = json.loads((tmp_path / "traces.jsonl").read_text())
    assert data["tool_calls"][0]["arguments"]["since"] == "<present>"
    assert data["tool_calls"][0]["arguments"].get("query") is None
