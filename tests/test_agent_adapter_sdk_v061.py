from __future__ import annotations

import threading

from adapters.sdk import BufferedAgentEventSink


def _event(event_id: str = "e1") -> dict:
    return {
        "event_id": event_id,
        "observed_at": "2026-09-25T00:00:00Z",
        "agent_name": "TestAgent",
        "operation": "tool_call",
        "status": "success",
        "observation_level": "native_trace",
        "run_id": "run-1",
        "trace_id": "trace-1",
        "tool_name": "read_file",
        "tool_category": "filesystem",
    }


def test_buffered_sink_batches_valid_structural_events():
    batches: list[list[dict]] = []
    sink = BufferedAgentEventSink(sender=lambda events: batches.append(events) or {}, flush_interval=0.02)
    assert sink.emit(_event("a")) is True
    assert sink.emit(_event("b")) is True
    assert sink.force_flush(timeout=1.0) is True
    stats = sink.stats()
    assert stats.accepted == 2
    assert stats.dropped == 0
    assert stats.send_failures == 0
    assert stats.batches_sent >= 1
    assert [event["event_id"] for batch in batches for event in batch] == ["a", "b"]
    sink.shutdown(timeout=1.0)


def test_buffered_sink_rejects_content_before_sender_sees_it():
    sent: list[list[dict]] = []
    sink = BufferedAgentEventSink(sender=lambda events: sent.append(events) or {}, flush_interval=0.02)
    unsafe = _event()
    unsafe["tool_input"] = {"secret": "SUPERSECRET"}
    assert sink.emit(unsafe) is False
    assert sink.force_flush(timeout=0.2) is True
    assert sent == []
    assert sink.stats().dropped == 1
    sink.shutdown(timeout=0.2)


def test_buffered_sink_network_failure_never_raises_to_agent_callback():
    def fail(_events):
        raise RuntimeError("SUPERSECRET network details")

    sink = BufferedAgentEventSink(sender=fail, flush_interval=0.02)
    assert sink.emit(_event()) is True
    assert sink.force_flush(timeout=1.0) is True
    stats = sink.stats()
    assert stats.accepted == 1
    assert stats.send_failures == 1
    assert stats.batches_sent == 0
    sink.shutdown(timeout=0.2)


def test_buffered_sink_drops_on_pressure_instead_of_blocking():
    sender_started = threading.Event()
    sender_release = threading.Event()

    def blocking_sender(_events):
        sender_started.set()
        sender_release.wait(timeout=1.0)
        return {}

    sink = BufferedAgentEventSink(
        sender=blocking_sender,
        max_queue=1,
        batch_size=1,
        flush_interval=0.02,
    )
    assert sink.emit(_event("first")) is True
    assert sender_started.wait(timeout=1.0)
    assert sink.emit(_event("queued")) is True
    assert sink.emit(_event("dropped")) is False
    sender_release.set()
    assert sink.force_flush(timeout=1.0) is True
    assert sink.stats().dropped == 1
    sink.shutdown(timeout=1.0)


def test_shutdown_makes_future_emits_fail_open():
    sink = BufferedAgentEventSink(sender=lambda _events: {}, flush_interval=0.02)
    sink.shutdown(timeout=0.2)
    assert sink.emit(_event()) is False
    assert sink.stats().dropped == 1
