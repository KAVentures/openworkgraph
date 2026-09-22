from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from shared.evidence import RAW_RICH_EVIDENCE_CONTRACT, rich_evidence_row
from shared.time_utils import normalize_optional_timestamp, normalize_timestamp
from .db import GatewayDB


def _encode_cursor(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {}
    except Exception:
        return {}


def _cursor_filter(state: dict[str, Any], key: str, requested: str | None) -> str | None:
    """Resolve cursor state without allowing it to widen an explicit request."""
    requested_value = str(requested or "").strip()
    if requested is not None:
        return requested_value or None
    cursor_value = str(state.get(key) or "").strip()
    return cursor_value or None


def workflow_trace(
    db: GatewayDB,
    *,
    organization_id: str,
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    query: str | None = None,
    actor_id: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    page_limit = max(1, min(int(limit), 500))
    state = _decode_cursor(cursor or "") if cursor else {}
    if cursor and not state:
        raise ValueError("invalid workflow-trace cursor")
    if state:
        snapshot_until_raw = str(state.get("snapshot_until") or "")
        if not snapshot_until_raw:
            raise ValueError("invalid workflow-trace cursor")
        snapshot_until = normalize_timestamp(snapshot_until_raw)
        since = _cursor_filter(state, "since", since)
        until = _cursor_filter(state, "until", until)
        query = _cursor_filter(state, "query", query)
        actor_id = _cursor_filter(state, "actor_id", actor_id)
        device_id = _cursor_filter(state, "device_id", device_id)
        session_id = _cursor_filter(state, "session_id", session_id)
        event_type = _cursor_filter(state, "event_type", event_type)
        after_at_raw = str(state.get("after_at") or "")
        after_event_id = str(state.get("after_event_id") or "") or None
        if not after_at_raw or not after_event_id:
            raise ValueError("invalid workflow-trace cursor")
        after_at = normalize_timestamp(after_at_raw)
    else:
        since = normalize_optional_timestamp(since)
        until = normalize_optional_timestamp(until)
        snapshot_until = until or normalize_timestamp(datetime.now(timezone.utc).isoformat())
        after_at = None
        after_event_id = None

    # Cursor copies of filters are not trusted as authorization state, but their
    # timestamp values still need the same canonical validation as first-page input.
    if state:
        since = normalize_optional_timestamp(since)
        until = normalize_optional_timestamp(until)

    rows = db.trace_rows(
        organization_id=organization_id,
        since=since,
        until=snapshot_until,
        after_at=after_at,
        after_event_id=after_event_id,
        query=query,
        actor_id=actor_id,
        device_id=device_id,
        session_id=session_id,
        event_type=event_type,
        limit=page_limit + 1,
    )
    has_more = len(rows) > page_limit
    visible = rows[:page_limit]
    rich = [rich_evidence_row(row, include_identity=True) for row in visible]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = _encode_cursor({
            "snapshot_until": snapshot_until,
            "since": since or "",
            "until": until or "",
            "query": query or "",
            "actor_id": actor_id or "",
            "device_id": device_id or "",
            "session_id": session_id or "",
            "event_type": event_type or "",
            "after_at": last.get("observed_at") or "",
            "after_event_id": last.get("event_id") or "",
        })
    return {
        "rows": rich,
        "returned": len(rich),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "snapshot_until": snapshot_until,
        "data_layer": "privacy_hardened_raw_rich_evidence",
        "evidence_contract": dict(RAW_RICH_EVIDENCE_CONTRACT),
        "derived_task_inference_authoritative": False,
    }
