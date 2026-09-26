from __future__ import annotations

"""Validated, privacy-safe ingestion for structural agent execution evidence."""

import json
from typing import Any

from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence
from shared.agent_ingress_validation import validate_agent_ingress_event
from shared.capture_control import filter_recordable
from shared.codex_otel_adapter import codex_otel_to_agent_events
from shared.otel_agent_adapter import otel_payload_to_agent_events
from .db import insert_events

MAX_AGENT_EVENTS = 500
MAX_AGENT_BATCH_BYTES = 2_000_000
MAX_OTEL_SPANS = 1000
MAX_CODEX_OTEL_RECORDS = 1000


def _bounded_json_size(value: Any, *, maximum: int = MAX_AGENT_BATCH_BYTES) -> None:
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except Exception as exc:
        raise AgentEvidenceError("agent payload is not JSON-serializable") from exc
    if size > maximum:
        raise AgentEvidenceError(f"agent payload exceeds {maximum} bytes")


def _validated_events(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Validate all untrusted events before projecting or writing any of them. This
    # preserves the existing all-or-nothing batch behavior for malformed input.
    return [agent_event_to_evidence(validate_agent_ingress_event(item)) for item in payloads]


def _insert_recordable(events: list[dict[str, Any]]) -> int:
    # Agent evidence obeys the same user-controlled Pause/Stop boundaries and
    # permanent deletion tombstones as desktop/browser evidence. This is applied
    # immediately before persistence so late or buffered agent delivery cannot
    # recreate skipped/deleted work.
    recordable, _suppressed = filter_recordable(events)
    return insert_events(recordable) if recordable else 0


def ingest_agent_payloads(payloads: list[dict[str, Any]]) -> dict[str, int]:
    if not isinstance(payloads, list):
        raise AgentEvidenceError("events must be a list")
    if not payloads:
        return {"received": 0, "inserted": 0}
    if len(payloads) > MAX_AGENT_EVENTS:
        raise AgentEvidenceError(f"agent batch exceeds {MAX_AGENT_EVENTS} events")
    _bounded_json_size(payloads)

    events = _validated_events(payloads)
    return {"received": len(payloads), "inserted": _insert_recordable(events)}


def ingest_otel_payload(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, int]:
    # The advertised request bound covers both the OTLP spans and the optional
    # OpenWorkGraph defaults. Do not let large defaults bypass the same limit.
    _bounded_json_size({"payload": payload, "defaults": defaults or {}})
    projected, stats = otel_payload_to_agent_events(
        payload,
        defaults=defaults,
        max_spans=MAX_OTEL_SPANS,
    )
    if len(projected) > MAX_AGENT_EVENTS * 2:
        raise AgentEvidenceError("OpenTelemetry projection produced too many agent events")
    events = _validated_events(projected)
    inserted = _insert_recordable(events)
    return {
        "spans_seen": int(stats.get("spans_seen") or 0),
        "spans_ignored": int(stats.get("spans_ignored") or 0),
        "projected": len(events),
        "inserted": inserted,
    }


def ingest_codex_otel_payload(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Ingest Codex OTLP JSON without persisting native diagnostic content."""
    _bounded_json_size({"payload": payload, "defaults": defaults or {}})
    projected, stats = codex_otel_to_agent_events(
        payload,
        defaults=defaults,
        max_records=MAX_CODEX_OTEL_RECORDS,
    )
    if len(projected) > MAX_AGENT_EVENTS * 2:
        raise AgentEvidenceError("Codex OpenTelemetry projection produced too many agent events")
    events = _validated_events(projected)
    inserted = _insert_recordable(events)
    return {
        "records_seen": int(stats.get("records_seen") or 0),
        "records_ignored": int(stats.get("records_ignored") or 0),
        "projected": len(events),
        "inserted": inserted,
    }
