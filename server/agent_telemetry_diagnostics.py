from __future__ import annotations

"""Structural delivery diagnostics for native agent telemetry channels.

Answers "is Claude Code sending anything, and if so what happens to it?" with
counts, timestamps and reason codes only. No payload, attribute value, header or
error text is ever stored here.

Per channel, a request is counted when it arrives, then as exactly one of:
rejected (with a reason code), observation off, or processed. Processed OTel
requests also report records seen/ignored by the structural allowlist and how
many events were stored versus suppressed by deletion/retention/pause windows.
"""

import threading
from datetime import datetime, timezone
from typing import Any

CHANNELS = {
    "claude_code_hooks": "Claude Code hooks",
    "claude_code_otel_logs": "Claude Code OpenTelemetry logs",
    "codex_otel": "Codex OpenTelemetry",
    "agent_events": "Other agent events (SDKs, adapters)",
    "otel_generic": "Generic OpenTelemetry",
    "spool": "Delayed delivery (agent spool)",
}
REJECTION_REASONS = ("auth", "invalid_payload", "too_large", "not_an_object", "adapter_error")

_LOCK = threading.Lock()
_STARTED_AT = datetime.now(timezone.utc).isoformat()
_STATS: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _channel(name: str) -> dict[str, Any]:
    return _STATS.setdefault(name, {
        "requests": 0,
        "processed": 0,
        "observation_off": 0,
        "rejected": {},
        "records_seen": 0,
        "records_ignored": 0,
        "events_projected": 0,
        "events_stored": 0,
        "last_received_at": None,
        "last_stored_at": None,
        "last_rejected_at": None,
        "last_rejection_reason": None,
    })


def received(channel: str) -> None:
    with _LOCK:
        entry = _channel(channel)
        entry["requests"] += 1
        entry["last_received_at"] = _now()


def rejected(channel: str, reason: str) -> None:
    reason = reason if reason in REJECTION_REASONS else "adapter_error"
    with _LOCK:
        entry = _channel(channel)
        entry["rejected"][reason] = int(entry["rejected"].get(reason, 0)) + 1
        entry["last_rejected_at"] = _now()
        entry["last_rejection_reason"] = reason


def observation_off(channel: str) -> None:
    with _LOCK:
        _channel(channel)["observation_off"] += 1


def processed(channel: str, result: dict[str, Any] | None = None) -> None:
    result = result or {}
    stored = int(result.get("inserted") or 0)
    projected = int(result.get("projected", result.get("received", stored)) or 0)
    seen = int(result.get("records_seen", result.get("spans_seen", 0)) or 0)
    ignored = int(result.get("records_ignored", result.get("spans_ignored", 0)) or 0)
    with _LOCK:
        entry = _channel(channel)
        entry["processed"] += 1
        entry["records_seen"] += seen
        entry["records_ignored"] += ignored
        entry["events_projected"] += projected
        entry["events_stored"] += stored
        if stored:
            entry["last_stored_at"] = _now()


def snapshot() -> dict[str, Any]:
    with _LOCK:
        channels = {
            name: {"label": label, **{k: (dict(v) if isinstance(v, dict) else v) for k, v in _channel(name).items()}}
            for name, label in CHANNELS.items()
        }
    return {"since": _STARTED_AT, "channels": channels}


def reset_for_tests() -> None:
    with _LOCK:
        _STATS.clear()
