from __future__ import annotations

import json

import pytest

from openworkgraph_agent import AgentObserver
from shared.agent_evidence import agent_event_to_evidence


def test_python_harness_sdk_emits_only_server_compatible_structural_events():
    batches: list[list[dict]] = []

    def sender(endpoint: str, token: str, events: list[dict]) -> None:
        assert endpoint.endswith("/agent-ingest/v1/events")
        assert token == "write-only-test-token"
        batches.append(events)

    observer = AgentObserver(
        "OpenClaw-style local harness",
        provider="local",
        framework="custom-harness",
        endpoint="http://127.0.0.1:8787/agent-ingest/v1/events",
        token="write-only-test-token",
        sender=sender,
        flush_interval=0.02,
    )
    with observer.run(run_id="run-1", workflow_id="workflow-1") as run:
        with run.model(model="example-model", usage={"input_tokens": 12, "output_tokens": 4}):
            pass
        with run.tool("repository_search", category="search"):
            returned_secret = "THIS_VALUE_MUST_NEVER_BE_TELEMETRY"
            assert returned_secret
        run.handoff()
        run.approval_requested()
        run.approval_received(approved=True)

    assert observer.flush(timeout=1.0) is True
    observer.shutdown(timeout=1.0)

    events = [event for batch in batches for event in batch]
    assert [event["operation"] for event in events] == [
        "run_started",
        "model_call",
        "tool_call",
        "handoff",
        "human_approval_requested",
        "human_approval_received",
        "run_finished",
    ]
    assert {event["run_id"] for event in events} == {"run-1"}
    assert {event["workflow_id"] for event in events} == {"workflow-1"}
    assert all(agent_event_to_evidence(event)["source"] == "agent" for event in events)
    serialized = json.dumps(events)
    assert "THIS_VALUE_MUST_NEVER_BE_TELEMETRY" not in serialized
    assert "prompt" not in serialized.lower()
    assert "response" not in serialized.lower()


def test_python_harness_sdk_never_serializes_exception_text_and_does_not_swallow_it():
    batches: list[list[dict]] = []
    observer = AgentObserver(
        "Harness",
        token="t",
        sender=lambda _endpoint, _token, events: batches.append(events),
        flush_interval=0.02,
    )

    with pytest.raises(RuntimeError, match="TOP_SECRET_EXCEPTION_TEXT"):
        with observer.run(run_id="run-error") as run:
            with run.tool("shell", category="shell"):
                raise RuntimeError("TOP_SECRET_EXCEPTION_TEXT")

    assert observer.flush(timeout=1.0) is True
    observer.shutdown(timeout=1.0)
    events = [event for batch in batches for event in batch]
    serialized = json.dumps(events)
    assert "TOP_SECRET_EXCEPTION_TEXT" not in serialized
    assert any(event["operation"] == "tool_call" and event["status"] == "error" for event in events)
    assert any(event["operation"] == "error" for event in events)
    assert any(event["operation"] == "run_finished" and event["status"] == "error" for event in events)


def test_python_harness_sdk_delivery_failure_is_fail_open():
    def fail(_endpoint: str, _token: str, _events: list[dict]) -> None:
        raise OSError("network details must not escape")

    observer = AgentObserver("Harness", token="t", sender=fail, flush_interval=0.02)
    with observer.run() as run:
        with run.tool("search", category="search"):
            pass
    assert observer.flush(timeout=1.0) is True
    stats = observer.stats()
    assert stats.accepted == 3
    assert stats.send_failures >= 1
    observer.shutdown(timeout=1.0)


def test_python_harness_sdk_rejects_non_structural_configuration():
    with pytest.raises(ValueError):
        AgentObserver("Harness", observation_level="full_transcript")
    observer = AgentObserver("Harness", token="t", sender=lambda *_: None)
    with observer.run() as run:
        with pytest.raises(ValueError):
            run.tool("anything", category="prompt_content")
    observer.shutdown(timeout=1.0)
