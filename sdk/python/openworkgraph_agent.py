from __future__ import annotations

"""Dependency-free OpenWorkGraph structural telemetry for custom Python agents.

This file can be copied directly into another harness. Its public API is
intentionally content-blind: no prompt, response, reasoning, tool arguments,
tool results, returned values, or exception text are accepted or serialized.
Delivery is bounded, asynchronous, and fail-open by default.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import queue
import threading
import time
from typing import Any, Callable, Mapping
from urllib import request as urllib_request
import uuid

OBSERVATION_LEVELS = frozenset({"native_trace", "instrumented_tools", "mcp_only", "os_observed", "outcome_only"})
TOOL_CATEGORIES = frozenset({
    "filesystem", "shell", "browser", "code", "search", "network", "database",
    "messaging", "issue_tracker", "deployment", "mcp", "other", "none",
})
USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _clean(value: Any, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _usage(value: Mapping[str, int] | None) -> dict[str, int]:
    if not value:
        return {}
    unknown = set(value) - USAGE_KEYS
    if unknown:
        raise ValueError(f"unsupported usage fields: {', '.join(sorted(unknown))}")
    out: dict[str, int] = {}
    for key, raw in value.items():
        amount = int(raw)
        if amount < 0:
            raise ValueError(f"{key} must be non-negative")
        out[key] = amount
    return out


def _default_sender(endpoint: str, token: str, events: list[dict]) -> None:
    if not token:
        raise RuntimeError("missing OWG agent-ingest token")
    body = json.dumps({"events": events}, separators=(",", ":")).encode("utf-8")
    req = urllib_request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib_request.urlopen(req, timeout=1.5) as response:
        if not 200 <= int(response.status) < 300:
            raise RuntimeError("OpenWorkGraph telemetry write failed")


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    trace_id: str
    workflow_id: str


@dataclass(frozen=True)
class ObserverStats:
    accepted: int
    dropped: int
    send_failures: int
    batches_sent: int
    queued: int


class _Delivery:
    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        sender: Callable[[str, str, list[dict]], None] | None = None,
        max_queue: int = 512,
        batch_size: int = 32,
        flush_interval: float = 0.2,
    ) -> None:
        self.endpoint = endpoint
        self.token = token
        self.sender = sender or _default_sender
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=max(1, min(int(max_queue), 10_000)))
        self.batch_size = max(1, min(int(batch_size), 256))
        self.flush_interval = max(0.02, min(float(flush_interval), 2.0))
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.accepted = 0
        self.dropped = 0
        self.send_failures = 0
        self.batches_sent = 0

    def _ensure_worker(self) -> None:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            if self.stop.is_set():
                return
            self.thread = threading.Thread(target=self._run, name="openworkgraph-agent-sdk", daemon=True)
            self.thread.start()

    def emit(self, event: dict) -> bool:
        if self.stop.is_set():
            with self.lock:
                self.dropped += 1
            return False
        self._ensure_worker()
        try:
            self.queue.put_nowait(dict(event))
        except queue.Full:
            with self.lock:
                self.dropped += 1
            return False
        with self.lock:
            self.accepted += 1
        return True

    def _take_batch(self) -> list[dict]:
        try:
            first = self.queue.get(timeout=self.flush_interval)
        except queue.Empty:
            return []
        batch = [first]
        while len(batch) < self.batch_size:
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _run(self) -> None:
        while not self.stop.is_set() or self.queue.unfinished_tasks:
            batch = self._take_batch()
            if not batch:
                if self.stop.is_set():
                    break
                continue
            try:
                self.sender(self.endpoint, self.token, batch)
            except Exception:
                with self.lock:
                    self.send_failures += 1
            else:
                with self.lock:
                    self.batches_sent += 1
            finally:
                for _ in batch:
                    self.queue.task_done()

    def flush(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, min(float(timeout), 10.0))
        while self.queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.queue.unfinished_tasks == 0

    def shutdown(self, timeout: float = 2.0) -> None:
        self.stop.set()
        self.flush(timeout)
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=max(0.0, min(float(timeout), 10.0)))

    def stats(self) -> ObserverStats:
        with self.lock:
            return ObserverStats(
                self.accepted,
                self.dropped,
                self.send_failures,
                self.batches_sent,
                self.queue.qsize(),
            )


class _TimedOperation(AbstractContextManager["_TimedOperation"]):
    def __init__(
        self,
        run: "AgentRun",
        *,
        operation: str,
        tool_name: str = "",
        tool_category: str = "none",
        model: str = "",
        usage: Mapping[str, int] | None = None,
    ) -> None:
        self.run = run
        self.operation = operation
        self.tool_name = _clean(tool_name, limit=200)
        self.tool_category = tool_category
        self.model_name = _clean(model, limit=200)
        self.token_usage = _usage(usage)
        self.started = 0.0
        self.span_id = _id("span")

    def __enter__(self) -> "_TimedOperation":
        self.started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.run._emit(
            self.operation,
            status="error" if exc_type is not None else "success",
            span_id=self.span_id,
            tool_name=self.tool_name,
            tool_category=self.tool_category,
            model=self.model_name,
            usage=self.token_usage,
            duration_seconds=max(0.0, time.monotonic() - self.started),
        )
        return False


class AgentRun(AbstractContextManager["AgentRun"]):
    def __init__(
        self,
        observer: "AgentObserver",
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        workflow_id: str | None = None,
        trigger_event_id: str | None = None,
    ) -> None:
        self.observer = observer
        self.run_id = _clean(run_id) or _id("run")
        self.trace_id = _clean(trace_id) or _id("trace")
        self.workflow_id = _clean(workflow_id)
        self.trigger_event_id = _clean(trigger_event_id)
        self.started = 0.0
        self.closed = False

    @property
    def identity(self) -> RunIdentity:
        return RunIdentity(self.run_id, self.trace_id, self.workflow_id)

    def __enter__(self) -> "AgentRun":
        self.started = time.monotonic()
        self._emit("run_started", status="running")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.closed:
            return False
        if exc_type is not None:
            self._emit("error", status="error")
        self._emit(
            "run_finished",
            status="error" if exc_type is not None else "success",
            duration_seconds=max(0.0, time.monotonic() - self.started),
        )
        self.closed = True
        return False

    def tool(self, name: str, *, category: str = "other") -> _TimedOperation:
        if category not in TOOL_CATEGORIES:
            raise ValueError(f"unsupported tool category: {category}")
        return _TimedOperation(self, operation="tool_call", tool_name=name, tool_category=category)

    def model(self, *, model: str = "", usage: Mapping[str, int] | None = None) -> _TimedOperation:
        return _TimedOperation(self, operation="model_call", model=model, usage=usage)

    def handoff(self) -> bool:
        return self._emit("handoff", status="success", span_id=_id("span"))

    def approval_requested(self) -> bool:
        return self._emit("human_approval_requested", status="running")

    def approval_received(self, *, approved: bool) -> bool:
        return self._emit("human_approval_received", status="success" if approved else "denied")

    def record_error(self) -> bool:
        return self._emit("error", status="error")

    def _emit(self, operation: str, **fields: Any) -> bool:
        return self.observer._emit(
            operation,
            run_id=self.run_id,
            trace_id=self.trace_id,
            workflow_id=self.workflow_id,
            trigger_event_id=self.trigger_event_id,
            **fields,
        )


class AgentObserver:
    """Observe any Python harness without putting OWG in its control path."""

    def __init__(
        self,
        agent_name: str,
        *,
        provider: str = "",
        framework: str = "custom",
        observation_level: str = "instrumented_tools",
        endpoint: str | None = None,
        token: str | None = None,
        device_id: str = "agent-local",
        sensor_id: str = "agent:python-sdk",
        sender: Callable[[str, str, list[dict]], None] | None = None,
        max_queue: int = 512,
        batch_size: int = 32,
        flush_interval: float = 0.2,
    ) -> None:
        name = _clean(agent_name, limit=160)
        if not name:
            raise ValueError("agent_name is required")
        if observation_level not in OBSERVATION_LEVELS:
            raise ValueError(f"unsupported observation level: {observation_level}")
        self.agent_name = name
        self.provider = _clean(provider, limit=160)
        self.framework = _clean(framework, limit=160)
        self.observation_level = observation_level
        self.device_id = _clean(device_id) or "agent-local"
        self.sensor_id = _clean(sensor_id) or "agent:python-sdk"
        self.delivery = _Delivery(
            endpoint=_clean(endpoint or os.getenv("OWG_AGENT_INGEST_URL") or "http://127.0.0.1:8787/agent-ingest/v1/events", limit=1000),
            token=_clean(token or os.getenv("OWG_AGENT_INGEST_TOKEN"), limit=4000),
            sender=sender,
            max_queue=max_queue,
            batch_size=batch_size,
            flush_interval=flush_interval,
        )

    def run(
        self,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        workflow_id: str | None = None,
        trigger_event_id: str | None = None,
    ) -> AgentRun:
        return AgentRun(
            self,
            run_id=run_id,
            trace_id=trace_id,
            workflow_id=workflow_id,
            trigger_event_id=trigger_event_id,
        )

    def _emit(self, operation: str, **fields: Any) -> bool:
        event = {
            "event_id": _id("evt"),
            "observed_at": _now(),
            "agent_name": self.agent_name,
            "provider": self.provider,
            "framework": self.framework,
            "operation": operation,
            "status": fields.pop("status", "unknown"),
            "observation_level": self.observation_level,
            "device_id": self.device_id,
            "sensor_id": self.sensor_id,
            "tool_category": fields.get("tool_category") or "none",
            **fields,
        }
        if event["tool_category"] not in TOOL_CATEGORIES:
            return False
        return self.delivery.emit(event)

    def flush(self, *, timeout: float = 2.0) -> bool:
        return self.delivery.flush(timeout)

    def shutdown(self, *, timeout: float = 2.0) -> None:
        self.delivery.shutdown(timeout)

    def stats(self) -> ObserverStats:
        return self.delivery.stats()


__all__ = ["AgentObserver", "AgentRun", "RunIdentity", "ObserverStats"]
