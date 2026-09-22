from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from shared.evidence import RAW_RICH_EVIDENCE_CONTRACT, rich_evidence_row
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


def workflow_trace(db: GatewayDB, *, organization_id: str, since: str | None = None, until: str | None = None, cursor: str | None = None, limit: int = 100, query: str | None = None, actor_id: str | None = None, device_id: str | None = None, session_id: str | None = None, event_type: str | None = None) -> dict[str, Any]:
    page_limit = max(1, min(int(limit), 500))
    state = _decode_cursor(cursor or "") if cursor else {}
    if state:
        snapshot_until = str(state.get("snapshot_until") or "")
        since = str(state.get("since") or "") or None
        until = str(state.get("until") or "") or None
        query = str(state.get("query") or "") or None
        actor_id = str(state.get("actor_id") or "") or None
        device_id = str(state.get("device_id") or "") or None
        session_id = str(state.get("session_id") or "") or None
        event_type = str(state.get("event_type") or "") or None
        after_at = str(state.get("after_at") or "") or None
        after_event_id = str(state.get("after_event_id") or "") or None
    else:
        snapshot_until = until or datetime.now(timezone.utc).isoformat()
        after_at = None
        after_event_id = None

    rows = db.trace_rows(organization_id=organization_id, since=since, until=snapshot_until, after_at=after_at, after_event_id=after_event_id, query=query, actor_id=actor_id, device_id=device_id, session_id=session_id, event_type=event_type, limit=page_limit + 1)
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
    }
