from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any

from .db import connect


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _meta_parts(meta: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    m = meta if isinstance(meta, dict) else {}
    page = m.get("page") if isinstance(m.get("page"), dict) else {}
    target = m.get("target") if isinstance(m.get("target"), dict) else {}
    return m, page, target


def _nested_number(meta: dict[str, Any], key: str) -> float | int:
    candidates: list[Any] = [meta]
    for name in ("activity", "effort", "timing", "input"):
        value = meta.get(name)
        if isinstance(value, dict):
            candidates.append(value)
    for source in candidates:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return 0


def compact_event(row: dict[str, Any]) -> dict[str, Any]:
    try:
        meta = json.loads(row.get("metadata_json") or "{}") if "metadata_json" in row else row.get("metadata") or {}
    except Exception:
        meta = {}
    meta, page, target = _meta_parts(meta)
    target_label = target.get("label") or target.get("title") or target.get("description") or target.get("help") or ""
    target_role = target.get("role") or target.get("localized_role") or target.get("tag") or ""
    return {
        "observed_at": row.get("observed_at"),
        "app": row.get("app"),
        "window_title": row.get("window_title"),
        "event_type": row.get("event_type"),
        "action": meta.get("action", ""),
        "target_label": target_label,
        "target_role": target_role,
        "page_host": page.get("hostname", ""),
        "page_path": page.get("pathname", ""),
        "duration_seconds": row.get("duration_seconds", 0) or 0,
        "foreground_seconds": _nested_number(meta, "foreground_seconds"),
        "engaged_seconds": _nested_number(meta, "engaged_seconds"),
        "active_input_seconds": _nested_number(meta, "active_input_seconds"),
        "idle_seconds": _nested_number(meta, "idle_seconds"),
        "keypress_count": _nested_number(meta, "keypress_count"),
        "click_count": _nested_number(meta, "click_count"),
        "scroll_count": _nested_number(meta, "scroll_count"),
        "source": row.get("source", ""),
        "session_id": row.get("session_id", ""),
    }


def workflow_trace(
    *,
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    scope: str = "current",
) -> dict[str, Any]:
    """Return one compact, stable, chronological page of rich local evidence."""
    page_limit = max(1, min(int(limit), 500))
    state = _decode_cursor(cursor or "") if cursor else {}

    if state:
        snapshot_until = str(state.get("snapshot_until") or "")
        effective_since = str(state.get("since") or "") or None
        effective_until = str(state.get("until") or "") or None
        after_at = str(state.get("after_at") or "") or None
        try:
            after_id = int(state.get("after_id") or 0)
        except Exception:
            after_id = 0
        effective_scope = str(state.get("scope") or "current")
    else:
        snapshot_until = until or datetime.now(timezone.utc).isoformat()
        effective_since = since
        effective_until = until
        after_at = None
        after_id = 0
        effective_scope = scope if scope in {"current", "all"} else "current"
        if effective_scope == "current" and not effective_since:
            effective_since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") or None

    clauses = ["observed_at <= ?"]
    params: list[Any] = [snapshot_until]
    if effective_since:
        clauses.append("observed_at >= ?")
        params.append(effective_since)
    if effective_until:
        clauses.append("observed_at <= ?")
        params.append(effective_until)
    if after_at:
        clauses.append("(observed_at > ? OR (observed_at = ? AND id > ?))")
        params.extend([after_at, after_at, after_id])

    where = " AND ".join(clauses)
    with connect() as conn:
        count_clauses = ["observed_at <= ?"]
        count_params: list[Any] = [snapshot_until]
        if effective_since:
            count_clauses.append("observed_at >= ?")
            count_params.append(effective_since)
        if effective_until:
            count_clauses.append("observed_at <= ?")
            count_params.append(effective_until)
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {' AND '.join(count_clauses)}",
            tuple(count_params),
        ).fetchone()[0])
        db_rows = conn.execute(
            f"SELECT * FROM events WHERE {where} ORDER BY observed_at ASC, id ASC LIMIT ?",
            tuple(params + [page_limit + 1]),
        ).fetchall()

    has_more = len(db_rows) > page_limit
    visible = db_rows[:page_limit]
    rows = [compact_event(dict(row)) for row in visible]
    next_cursor = None
    if has_more and visible:
        last = dict(visible[-1])
        next_cursor = _encode_cursor({
            "snapshot_until": snapshot_until,
            "since": effective_since or "",
            "until": effective_until or "",
            "scope": effective_scope,
            "after_at": last.get("observed_at"),
            "after_id": last.get("id"),
        })

    return {
        "rows": rows,
        "returned": len(rows),
        "total": total,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "snapshot_until": snapshot_until,
        "scope": effective_scope,
        "since": effective_since,
        "until": effective_until,
        "data_layer": "rich_local_evidence_compact",
    }
