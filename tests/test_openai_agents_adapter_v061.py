from __future__ import annotations

import json
from datetime import datetime

from adapters.openai_agents import OpenWorkGraphTracingProcessor


class CaptureSink:
    def __init__(self):
        self.events: list[dict] = []
        self.flushes = 0
        self.shutdowns = 0

    def emit(self, event: dict) -> bool:
        self.events.append(dict(event))
        return True

    def force_flush(self, *, timeout: float = 2.0) -> bool:
        self.flushes += 1
        return True

    def shutdown(self, *, timeout: float = 2.0) -> None:
        self.shutdowns += 1


class FakeTrace:
    def __init__(self):
        self.trace_id = "trace_patient@example.com_SUPERSECRET"
        self.name = "Patient Anna Svensson workflow"
        self.group_id = "customer-739201"
        self.metadata = {"prompt": "SUPERSECRET", "customer": "Anna Svensson"}


class FakeData:
    def __init__(self, kind: str, **kwargs):
        self.type = kind
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeSpan:
    def __init__(
        self,
        *,
        span_id: str,
        trace_id: str,
        parent_id: str = "",
        data: FakeData,
        error=None,
        started_at: str = "2026-09-25T00:00:00Z",
        ended_at: str = "2026-09-25T00:00:01Z",
    ):
        self.span_id = span_id
        self.trace_id = trace_id
        self.parent_id = parent_id or None
        self.span_data = data
        self.error = error
        self.started_at = started_at
        self.ended_at = ended_at
        self.trace_metadata = {"prompt": "SUPERSECRET metadata"}


def test_trace_events_hash_ids_and_ignore_name_group_and_metadata():
    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    trace = FakeTrace()

    processor.on_trace_start(trace)
    processor.on_trace_end(trace)

    assert [e["operation"] for e in sink.events] == ["run_started", "run_finished"]
    assert sink.events[0]["agent_name"] == "OpenAI-Agents-SDK"
    assert sink.events[0]["run_id"].startswith("trace:")
    assert sink.events[0]["trace_id"] == sink.events[0]["run_id"]
    serialized = json.dumps(sink.events)
    for forbidden in (
        "patient@example.com",
        "SUPERSECRET",
        "Anna Svensson",
        "customer-739201",
        "Patient Anna Svensson workflow",
    ):
        assert forbidden not in serialized


def test_function_span_keeps_structure_and_never_reads_input_output_or_mcp_payload():
    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    trace_id = "trace_0123456789abcdef0123456789abcdef"

    agent_span = FakeSpan(
        span_id="span_agent",
        trace_id=trace_id,
        data=FakeData("agent", name="ResearchAgent", tools=["private"], handoffs=["private"]),
    )
    processor.on_span_start(agent_span)

    function_span = FakeSpan(
        span_id="span_tool_patient@example.com",
        trace_id=trace_id,
        parent_id="span_agent",
        data=FakeData(
            "function",
            name="github_search",
            input="SUPERSECRET prompt-like arguments",
            output={"email": "patient@example.com"},
            mcp_data={"server": "secret-server", "payload": "SUPERSECRET"},
        ),
    )
    processor.on_span_start(function_span)
    processor.on_span_end(function_span)

    [event] = sink.events
    assert event["operation"] == "tool_call"
    assert event["status"] == "success"
    assert event["agent_name"] == "ResearchAgent"
    assert event["tool_name"] == "github_search"
    assert event["tool_category"] == "mcp"
    assert event["duration_seconds"] == 1.0
    assert event["span_id"].startswith("span:")
    assert event["parent_span_id"].startswith("span:")
    serialized = json.dumps(event)
    for forbidden in ("SUPERSECRET", "patient@example.com", "secret-server"):
        assert forbidden not in serialized


def test_dynamic_or_sensitive_tool_and_agent_names_fall_back():
    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    trace_id = "trace_0123456789abcdef0123456789abcdef"
    agent_span = FakeSpan(
        span_id="span_agent",
        trace_id=trace_id,
        data=FakeData("agent", name="Anna Svensson Support Agent"),
    )
    processor.on_span_start(agent_span)
    tool_span = FakeSpan(
        span_id="span_tool",
        trace_id=trace_id,
        parent_id="span_agent",
        data=FakeData(
            "function",
            name="email_patient@example.com",
            input="SUPERSECRET",
            output="SUPERSECRET",
            mcp_data=None,
        ),
    )
    processor.on_span_start(tool_span)
    processor.on_span_end(tool_span)

    [event] = sink.events
    assert event["agent_name"] == "OpenAI-Agent"
    assert event["tool_name"] == "unknown-tool"
    serialized = json.dumps(event)
    assert "Anna Svensson" not in serialized
    assert "patient@example.com" not in serialized


def test_generation_span_keeps_model_usage_and_drops_messages_config_and_error_text():
    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    trace_id = "trace_0123456789abcdef0123456789abcdef"
    generation = FakeSpan(
        span_id="span_generation",
        trace_id=trace_id,
        data=FakeData(
            "generation",
            input=[{"role": "user", "content": "SUPERSECRET"}],
            output=[{"role": "assistant", "content": "patient@example.com"}],
            model="gpt-5.6",
            model_config={"secret": "SUPERSECRET"},
            usage={
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
                "sensitive_breakdown": "SUPERSECRET",
            },
        ),
        error={"message": "patient@example.com SUPERSECRET"},
    )
    processor.on_span_start(generation)
    processor.on_span_end(generation)

    [event] = sink.events
    assert event["operation"] == "model_call"
    assert event["status"] == "error"
    assert event["model"] == "gpt-5.6"
    assert event["usage"] == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
    serialized = json.dumps(event)
    assert "SUPERSECRET" not in serialized
    assert "patient@example.com" not in serialized
    assert "sensitive_breakdown" not in serialized


def test_handoff_records_only_structural_transition_not_native_agent_names():
    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    trace_id = "trace_0123456789abcdef0123456789abcdef"
    parent = FakeSpan(
        span_id="span_agent",
        trace_id=trace_id,
        data=FakeData("agent", name="RouterAgent"),
    )
    processor.on_span_start(parent)
    handoff = FakeSpan(
        span_id="span_handoff",
        trace_id=trace_id,
        parent_id="span_agent",
        data=FakeData(
            "handoff",
            from_agent="Anna Svensson",
            to_agent="patient@example.com",
        ),
    )
    processor.on_span_start(handoff)
    processor.on_span_end(handoff)

    [event] = sink.events
    assert event["operation"] == "handoff"
    assert event["agent_name"] == "RouterAgent"
    serialized = json.dumps(event)
    assert "Anna Svensson" not in serialized
    assert "patient@example.com" not in serialized


def test_unknown_content_heavy_span_types_are_ignored():
    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    span = FakeSpan(
        span_id="span_custom",
        trace_id="trace_0123456789abcdef0123456789abcdef",
        data=FakeData("custom", data={"prompt": "SUPERSECRET"}, name="secret"),
    )
    processor.on_span_start(span)
    processor.on_span_end(span)
    assert sink.events == []


def test_processor_flush_and_shutdown_delegate_without_exposing_runtime_state():
    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    processor.force_flush()
    processor.shutdown()
    assert sink.flushes == 1
    assert sink.shutdowns == 1


def test_trace_processor_never_reads_trace_name_group_metadata_or_export():
    class PoisonTrace:
        trace_id = "trace_0123456789abcdef0123456789abcdef"

        @property
        def name(self):
            raise AssertionError("trace name must never be read")

        @property
        def group_id(self):
            raise AssertionError("trace group_id must never be read")

        @property
        def metadata(self):
            raise AssertionError("trace metadata must never be read")

        def export(self):
            raise AssertionError("native trace export must never be called")

    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    trace = PoisonTrace()
    processor.on_trace_start(trace)
    processor.on_trace_end(trace)
    assert [event["operation"] for event in sink.events] == ["run_started", "run_finished"]


def test_function_processor_never_reads_input_output_or_export():
    class PoisonFunctionData:
        type = "function"
        name = "safe_tool"
        mcp_data = None

        @property
        def input(self):
            raise AssertionError("function input must never be read")

        @property
        def output(self):
            raise AssertionError("function output must never be read")

        def export(self):
            raise AssertionError("native span-data export must never be called")

    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    span = FakeSpan(
        span_id="span_safe",
        trace_id="trace_0123456789abcdef0123456789abcdef",
        data=PoisonFunctionData(),
    )
    span.export = lambda: (_ for _ in ()).throw(AssertionError("native span export must never be called"))
    processor.on_span_start(span)
    processor.on_span_end(span)
    [event] = sink.events
    assert event["tool_name"] == "safe_tool"
    assert event["operation"] == "tool_call"


def test_malformed_timestamp_cannot_become_stored_content():
    sink = CaptureSink()
    processor = OpenWorkGraphTracingProcessor(sink=sink)
    span = FakeSpan(
        span_id="span_timestamp",
        trace_id="trace_0123456789abcdef0123456789abcdef",
        data=FakeData("function", name="safe_tool", mcp_data=None),
        started_at="patient@example.com SUPERSECRET start",
        ended_at="patient@example.com SUPERSECRET end",
    )
    processor.on_span_start(span)
    processor.on_span_end(span)
    [event] = sink.events
    assert event["duration_seconds"] == 0.0
    assert "SUPERSECRET" not in event["observed_at"]
    assert "patient@example.com" not in event["observed_at"]
    datetime.fromisoformat(event["observed_at"])
