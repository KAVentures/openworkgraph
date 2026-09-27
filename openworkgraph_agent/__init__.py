from __future__ import annotations

"""Tiny public SDK for emitting privacy-safe structural agent telemetry.

The SDK is intentionally content-blind: its public API has no prompt, response,
tool-argument, tool-result, or reasoning fields. Delivery is fail-open and uses
the same bounded background sink as OpenWorkGraph's native adapters.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
import time
import uuid
from typing import Any, Callable, Mapping

from adapters.sdk import BufferedAgentEventSink
from shared.agent_evidence import (
    AGENT_OBSERVATION_LEVELS,
    AGENT_TOOL_CATEGORIES,
    AgentEvidenceError,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _clean(value: str | None, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _usage(value: Mapping[str, int] | None) -> dict[str, int]:
    if not value:
        return {}
    allowed = {"input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unsupported usage fields: {', '.join(sorted(unknown))}")
    out: dict[str, int] = {}
    for key, raw in value.items():
        amount = int(raw)
        if amount < 0:
            raise ValueError(f"{key} must be non-negative")
        out[key] = amount
    return out


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    trace_id: str
    workflow_id: str


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
        self._run = run
        self._operation = operation
        self._tool_name = _clean(tool_name, limit=200)
        self._tool_category = tool_category
        self._model = _clean(model, limit=200)
        self._usage = _usage(usage)
        self._started = 0.0
        self.span_id = _id("span")

    def __enter__(self) -> "_TimedOperation":
        self._started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = max(0.0, time.monotonic() - self._started)
        self._run._emit(
            self._operation,
            status="error" if exc_type is not None else "success",
            span_id=self.span_id,
            tool_name=self._tool_name,
            tool_category=self._tool_category,
            model=self._model,
            usage=self._usage,
            duration_seconds=duration,
        )
        return False


class AgentRun(AbstractContextManager["AgentRun"]):
    """One harness run. Methods emit structural facts only."""

    def __init__(
        self,
        observer: "AgentObserver",
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        workflow_id: str | None = None,
        trigger_event_id: str | None = None,
    ) -> None:
        self._observer = observer
        self.run_id = _clean(run_id) or _id("run")
        self.trace_id = _clean(trace_id) or _id("trace")
        self.workflow_id = _clean(workflow_id)
        self.trigger_event_id = _clean(trigger_event_id)
        self._started_at = 0.0
        self._closed = False

    @property
    def identity(self) -> RunIdentity:
        return RunIdentity(self.run_id, self.trace_id, self.workflow_id)

    def __enter__(self) -> "AgentRun":
        self._started_at = time.monotonic()
        self._emit("run_started", status="running")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._closed:
            return False
        if exc_type is not None:
            # Error existence is useful structural evidence. Exception text is
            # deliberately not accepted or transmitted by this SDK.
            self._emit("error", status="error")
        duration = max(0.0, time.monotonic() - self._started_at)
        self._emit(
            "run_finished",
            status="error" if exc_type is not None else "success",
            duration_seconds=duration,
        )
        self._closed = True
        return False

    def tool(self, name: str, *, category: str = "other") -> _TimedOperation:
        if category not in AGENT_TOOL_CATEGORIES:
            raise ValueError(f"unsupported tool category: {category}")
        return _TimedOperation(
            self,
            operation="tool_call",
            tool_name=name,
            tool_category=category,
        )

    def model(self, *, model: str = "", usage: Mapping[str, int] | None = None) -> _TimedOperation:
        return _TimedOperation(self, operation="model_call", model=model, usage=usage)

    def handoff(self, *, span_id: str | None = None, parent_span_id: str | None = None) -> bool:
        return self._emit(
            "handoff",
            status="success",
            span_id=_clean(span_id) or _id("span"),
            parent_span_id=_clean(parent_span_id),
        )

    def approval_requested(self) -> bool:
        return self._emit("human_approval_requested", status="running")

    def approval_received(self, *, approved: bool) -> bool:
        return self._emit("human_approval_received", status="success" if approved else "denied")

    def record_error(self) -> bool:
        """Record that an error occurred without accepting error text/content."""
        return self._emit("error", status="error")

    def _emit(self, operation: str, **fields: Any) -> bool:
        return self._observer._emit(
            operation,
            run_id=self.run_id,
            trace_id=self.trace_id,
            workflow_id=self.workflow_id,
            trigger_event_id=self.trigger_event_id,
            **fields,
        )


class AgentObserver:
    """Fail-open structural telemetry for any Python agent or harness.

    A caller may inject ``sender`` in tests or advanced embeddings. Normal use
    relies on the installation-local write-only agent ingest credential already
    used by OpenWorkGraph's native adapters.
    """

    def __init__(
        self,
        agent_name: str,
        *,
        provider: str = "",
        framework: str = "custom",
        observation_level: str = "instrumented_tools",
        device_id: str = "agent-local",
        sensor_id: str = "agent:python-sdk",
        sender: Callable[[list[dict]], dict] | None = None,
    ) -> None:
        name = _clean(agent_name, limit=160)
        if not name:
            raise ValueError("agent_name is required")
        if observation_level not in AGENT_OBSERVATION_LEVELS:
            raise ValueError(f"unsupported observation level: {observation_level}")
        self.agent_name = name
        self.provider = _clean(provider, limit=160)
        self.framework = _clean(framework, limit=160)
        self.observation_level = observation_level
        self.device_id = _clean(device_id) or "agent-local"
        self.sensor_id = _clean(sensor_id) or "agent:python-sdk"
        self._sink = BufferedAgentEventSink(sender=sender)

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
            **fields,
        }
        # Validate before it reaches the asynchronous sink so programming errors
        # are fail-closed for telemetry but remain fail-open for the agent.
        try:
            return self._sink.emit(event)
        except (AgentEvidenceError, TypeError, ValueError):
            return False

    def flush(self, *, timeout: float = 2.0) -> bool:
        return self._sink.force_flush(timeout=timeout)

    def shutdown(self, *, timeout: float = 2.0) -> None:
        self._sink.shutdown(timeout=timeout)

    def stats(self):
        return self._sink.stats()


__all__ = ["AgentObserver", "AgentRun", "RunIdentity"]
