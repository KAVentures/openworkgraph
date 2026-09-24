from __future__ import annotations

"""Validated, privacy-safe ingestion for structural agent execution evidence."""

import json
from typing import Any

from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence
from shared.otel_agent_adapter import otel_payload_to_agent_events
from .db import insert_events

MAX_AGENT_EVENTS = 500
MAX_AGENT_BATCH_BYTES = 2_000_000
MAX_OTEL_SPANS = 1000


def _bounded_json_size(value: Any, *, maximum: int = MAX_AGENT_BATCH_BYTES) -> None:
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except Exception as exc:
        raise AgentEvidenceError("agent payload is not JSON-serializable") from exc
    if size > maximum:
        raise AgentEvidenceError(f"agent payload exceeds {maximum} bytes")


def ingest_agent_payloads(payloads: list[dict[str, Any]]) -> dict[str, int]:
    if not isinstance(payloads, list):
        raise AgentEvidenceError("events must be a list")
    if not payloads:
        return {"received": 0, "inserted": 0}
    if len(payloads) > MAX_AGENT_EVENTS:
        raise AgentEvidenceError(f"agent batch exceeds {MAX_AGENT_EVENTS} events")
    _bounded_json_size(payloads)

    # Validate and project the entire batch before the first database write so a
    # malformed final event cannot leave a partially accepted batch behind.
    events = [agent_event_to_evidence(item) for item in payloads]
    return {"received": len(payloads), "inserted": insert_events(events)}


def ingest_otel_payload(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, int]:
    _bounded_json_size(payload)
    projected, stats = otel_payload_to_agent_events(
        payload,
        defaults=defaults,
        max_spans=MAX_OTEL_SPANS,
    )
    if len(projected) > MAX_AGENT_EVENTS * 2:
        raise AgentEvidenceError("OpenTelemetry projection produced too many agent events")
    events = [agent_event_to_evidence(item) for item in projected]
    inserted = insert_events(events) if events else 0
    return {
        "spans_seen": int(stats.get("spans_seen") or 0),
        "spans_ignored": int(stats.get("spans_ignored") or 0),
        "projected": len(events),
        "inserted": inserted,
    }
