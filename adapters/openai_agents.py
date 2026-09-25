from __future__ import annotations

"""Privacy-minimized OpenAI Agents SDK tracing processor.

The SDK's native tracing objects can contain prompts, generated output, tool
arguments/results, arbitrary metadata, group IDs and caller-supplied identifiers.
This adapter never serializes or exports those objects. It inspects only a small
structural allowlist and sends normalized events through the generic OWG adapter
sink.
"""

from datetime import datetime, timezone
import hashlib
import os
import re
import sys
import threading
from typing import Any

from .sdk import BufferedAgentEventSink

try:  # Optional dependency: OpenWorkGraph itself does not require openai-agents.
    from agents.tracing.processor_interface import TracingProcessor as _TracingProcessor
except Exception:  # pragma: no cover - exercised when the optional SDK is absent.
    class _TracingProcessor:  # type: ignore[no-redef]
        pass


_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,199}$")
_USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_label(value: Any, *, default: str = "", limit: int = 160) -> str:
    raw = _text(value, min(limit, 200))
    if not raw or not _SAFE_LABEL.fullmatch(raw):
        return default
    return raw[:limit]


def _opaque_id(prefix: str, value: Any) -> str:
    """Map even caller-supplied SDK IDs to unlinkable local structural IDs."""
    raw = _text(value, 500)
    if not raw:
        return ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def _event_id(kind: str, trace_id: Any, span_id: Any = "") -> str:
    material = "\x1f".join((_text(kind, 80), _text(trace_id, 500), _text(span_id, 500))).encode("utf-8")
    return "openai-agents:" + hashlib.sha256(material).hexdigest()[:40]


def _parse_iso(value: Any) -> datetime | None:
    raw = _text(value, 100)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _safe_timestamp(value: Any) -> str:
    parsed = _parse_iso(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed is not None else _now_iso()


def _duration_seconds(span: Any) -> float:
    started = _parse_iso(getattr(span, "started_at", None))
    ended = _parse_iso(getattr(span, "ended_at", None))
    if started is None or ended is None:
        return 0.0
    return round(max(0.0, min((ended - started).total_seconds(), 7 * 24 * 60 * 60)), 6)


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key in _USAGE_KEYS:
        if key not in value:
            continue
        try:
            amount = int(value[key])
        except Exception:
            continue
        if 0 <= amount <= 1_000_000_000:
            out[key] = amount
    return out


def _tool_category(name: str, *, is_mcp: bool = False) -> str:
    if is_mcp:
        return "mcp"
    low = name.lower()
    if any(token in low for token in ("exec", "shell", "terminal", "bash", "powershell", "command")):
        return "shell"
    if any(token in low for token in ("read", "write", "edit", "file", "filesystem", "notebook")):
        return "filesystem"
    if any(token in low for token in ("search", "grep", "find", "lookup", "retrieval")):
        return "search"
    if any(token in low for token in ("browser", "playwright", "selenium", "web")):
        return "browser"
    if any(token in low for token in ("github", "git", "repository", "code")):
        return "code"
    if any(token in low for token in ("sql", "database", "postgres", "supabase")):
        return "database"
    if any(token in low for token in ("deploy", "vercel", "release")):
        return "deployment"
    return "other" if name else "none"


class OpenWorkGraphTracingProcessor(_TracingProcessor):
    """Additional OpenAI Agents SDK tracing processor with fail-open delivery."""

    def __init__(self, *, sink: Any | None = None) -> None:
        self._sink = sink or BufferedAgentEventSink()
        self._lock = threading.RLock()
        self._parent_by_span: dict[str, str] = {}
        self._agent_by_span: dict[str, str] = {}
        self._trace_by_span: dict[str, str] = {}

    def _debug_notice(self) -> None:
        if os.getenv("OWG_AGENT_ADAPTER_DEBUG", "").strip() == "1":
            print("OpenWorkGraph OpenAI Agents adapter skipped one trace event", file=sys.stderr)

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self._sink.emit(event)
        except Exception:
            self._debug_notice()

    def _safe_trace(self, trace_id: Any) -> str:
        return _opaque_id("trace", trace_id)

    def _safe_span(self, span_id: Any) -> str:
        return _opaque_id("span", span_id)

    def _remember_span(self, span: Any) -> tuple[str, str, str]:
        raw_span = _text(getattr(span, "span_id", ""), 500)
        raw_trace = _text(getattr(span, "trace_id", ""), 500)
        raw_parent = _text(getattr(span, "parent_id", ""), 500)
        if not raw_span or not raw_trace:
            return raw_span, raw_trace, raw_parent
        data = getattr(span, "span_data", None)
        span_type = _text(getattr(data, "type", ""), 80).lower()
        with self._lock:
            self._trace_by_span[raw_span] = raw_trace
            if raw_parent:
                self._parent_by_span[raw_span] = raw_parent
            if span_type == "agent":
                self._agent_by_span[raw_span] = _safe_label(
                    getattr(data, "name", ""),
                    default="OpenAI-Agent",
                    limit=160,
                )
        return raw_span, raw_trace, raw_parent

    def _nearest_agent(self, raw_span: str, raw_parent: str) -> str:
        with self._lock:
            current = raw_span
            seen: set[str] = set()
            while current and current not in seen:
                seen.add(current)
                if current in self._agent_by_span:
                    return self._agent_by_span[current]
                current = self._parent_by_span.get(current, "")
            current = raw_parent
            while current and current not in seen:
                seen.add(current)
                if current in self._agent_by_span:
                    return self._agent_by_span[current]
                current = self._parent_by_span.get(current, "")
        return "OpenAI-Agent"

    def _forget_span(self, raw_span: str) -> None:
        if not raw_span:
            return
        with self._lock:
            self._parent_by_span.pop(raw_span, None)
            self._agent_by_span.pop(raw_span, None)
            self._trace_by_span.pop(raw_span, None)

    def _forget_trace(self, raw_trace: str) -> None:
        if not raw_trace:
            return
        with self._lock:
            doomed = [span_id for span_id, trace_id in self._trace_by_span.items() if trace_id == raw_trace]
            for span_id in doomed:
                self._parent_by_span.pop(span_id, None)
                self._agent_by_span.pop(span_id, None)
                self._trace_by_span.pop(span_id, None)

    def on_trace_start(self, trace: Any) -> None:
        try:
            raw_trace = _text(getattr(trace, "trace_id", ""), 500)
            safe_trace = self._safe_trace(raw_trace)
            if not safe_trace:
                return
            self._emit({
                "event_id": _event_id("trace-start", raw_trace),
                "observed_at": _now_iso(),
                "agent_name": "OpenAI-Agents-SDK",
                "provider": "openai",
                "framework": "openai-agents-python",
                "operation": "run_started",
                "status": "running",
                "observation_level": "native_trace",
                "run_id": safe_trace,
                "trace_id": safe_trace,
            })
        except Exception:
            self._debug_notice()

    def on_trace_end(self, trace: Any) -> None:
        try:
            raw_trace = _text(getattr(trace, "trace_id", ""), 500)
            safe_trace = self._safe_trace(raw_trace)
            if safe_trace:
                self._emit({
                    "event_id": _event_id("trace-end", raw_trace),
                    "observed_at": _now_iso(),
                    "agent_name": "OpenAI-Agents-SDK",
                    "provider": "openai",
                    "framework": "openai-agents-python",
                    "operation": "run_finished",
                    "status": "unknown",
                    "observation_level": "native_trace",
                    "run_id": safe_trace,
                    "trace_id": safe_trace,
                })
            self._forget_trace(raw_trace)
        except Exception:
            self._debug_notice()

    def on_span_start(self, span: Any) -> None:
        try:
            self._remember_span(span)
        except Exception:
            self._debug_notice()

    def on_span_end(self, span: Any) -> None:
        raw_span = ""
        try:
            raw_span, raw_trace, raw_parent = self._remember_span(span)
            if not raw_span or not raw_trace:
                return
            data = getattr(span, "span_data", None)
            span_type = _text(getattr(data, "type", ""), 80).lower()
            if span_type not in {"generation", "response", "function", "handoff", "transcription", "speech"}:
                return

            safe_trace = self._safe_trace(raw_trace)
            safe_span = self._safe_span(raw_span)
            parent_span = self._safe_span(raw_parent)
            observed_at = _safe_timestamp(getattr(span, "ended_at", None))
            status = "error" if getattr(span, "error", None) is not None else "success"
            agent_name = self._nearest_agent(raw_span, raw_parent)
            base: dict[str, Any] = {
                "event_id": _event_id(span_type, raw_trace, raw_span),
                "observed_at": observed_at,
                "agent_name": agent_name,
                "provider": "openai",
                "framework": "openai-agents-python",
                "status": status,
                "observation_level": "native_trace",
                "run_id": safe_trace,
                "trace_id": safe_trace,
                "span_id": safe_span,
                "parent_span_id": parent_span,
                "duration_seconds": _duration_seconds(span),
            }

            if span_type in {"generation", "response", "transcription", "speech"}:
                base["operation"] = "model_call"
                if span_type != "response":
                    base["model"] = _safe_label(getattr(data, "model", ""), limit=200)
                if span_type == "generation":
                    base["usage"] = _usage(getattr(data, "usage", None))
                self._emit(base)
                return

            if span_type == "function":
                tool_name = _safe_label(getattr(data, "name", ""), default="unknown-tool", limit=160)
                base.update({
                    "operation": "tool_call",
                    "tool_name": tool_name,
                    "tool_category": _tool_category(
                        tool_name,
                        is_mcp=bool(getattr(data, "mcp_data", None)),
                    ),
                })
                self._emit(base)
                return

            if span_type == "handoff":
                base["operation"] = "handoff"
                self._emit(base)
        except Exception:
            self._debug_notice()
        finally:
            self._forget_span(raw_span)

    def force_flush(self) -> None:
        try:
            self._sink.force_flush(timeout=2.0)
        except Exception:
            self._debug_notice()

    def shutdown(self) -> None:
        try:
            self._sink.shutdown(timeout=2.0)
        except Exception:
            self._debug_notice()


def install_openai_agents_processor(*, sink: Any | None = None) -> OpenWorkGraphTracingProcessor:
    """Register OWG as an *additional* processor without replacing SDK tracing."""
    try:
        from agents.tracing import add_trace_processor
    except Exception as exc:
        raise RuntimeError(
            "OpenAI Agents SDK is not installed; install openai-agents in the agent application environment"
        ) from exc

    processor = OpenWorkGraphTracingProcessor(sink=sink)
    add_trace_processor(processor)
    return processor
