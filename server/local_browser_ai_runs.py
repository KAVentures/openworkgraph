"""Consent-scoped, content-free local Web AI run reconstruction.

Reuses hosted's already-tested same-tab new-chat correlation, never captures
prompts, response text, page titles, or URLs. This is a read-only projection
over canonical browser events; deletion and history grants apply immediately.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from gateway.browser_ai_run_recall import browser_ai_agent_runs


def _utc(value: Any) -> str | None:
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return result.astimezone(timezone.utc).isoformat() if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def local_web_ai_runs(
    connection: Any, *,
    lease: dict[str, Any],
    run_started_at: str | None,
    limit: int = 20,
    evidence_limit: int = 25000,
    execution_id: str = "",
    since: str | None = None,
) -> dict[str, Any]:
    mode = str(lease.get("mode") or "off")
    fences: list[str] = []
    params: list[Any] = []
    current = _utc(run_started_at)
    if current:
        fences.append("julianday(observed_at) >= julianday(?)")
        params.append(current)
    if mode == "all_saved":
        fences, params = ["1=1"], []
    elif mode == "selected_range":
        since, until = _utc(lease.get("since")), _utc(lease.get("until"))
        if since and until and since < until:
            fences.append("(julianday(observed_at) >= julianday(?) AND julianday(observed_at) < julianday(?))")
            params.extend((since, until))
    elif mode != "off":
        mode = "off"
    if not fences:
        return {"executions": [], "returned": 0, "source": "browser_ai_lifecycle",
                "history_access_limited": True, "outcome_is_verified": False}
    page = max(1, min(50000, int(evidence_limit)))
    clause = " OR ".join("(" + fragment + ")" for fragment in fences)
    # An explicit narrower requested interval never overrides the grant.
    requested_since = _utc(since) if since else None
    if since and not requested_since:
        raise ValueError("since must be timezone-aware ISO-8601")
    extra_clause = " AND julianday(observed_at) >= julianday(?)" if requested_since else ""
    if requested_since:
        params.append(requested_since)
    rows = connection.execute(
        "SELECT event_id, observed_at, source, session_id, event_type, metadata_json "
        "FROM events WHERE source='browser_extension' AND "
        "(event_type GLOB 'browser_agent_*' OR event_type='browser_resource_reference_observed') "
        f"AND ({clause}){extra_clause} ORDER BY julianday(observed_at) DESC, id DESC LIMIT ?",
        tuple(params + [page]),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in reversed(rows):
        raw = dict(row)
        try:
            meta = json.loads(str(raw.pop("metadata_json") or "{}"))
        except (TypeError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        # The hosted projector only reads allowlisted structural metadata.
        events.append({**raw, "metadata": meta})
    summaries = browser_ai_agent_runs(events)
    if execution_id:
        summaries = [s for s in summaries if s.get("execution_id") == execution_id]
    summaries = summaries[:max(1, min(100, int(limit)))]
    return {
        "executions": summaries,
        "returned": len(summaries),
        "source": "browser_ai_lifecycle",
        "evidence_rows_considered": len(rows),
        "evidence_limit_reached": len(rows) >= page,
        "history_access_limited": mode != "all_saved",
        "outcome_is_verified": False,
        "resource_refs_are_opaque": True,
        "content_captured": False,
        "read_only": True,
    }


__all__ = ["local_web_ai_runs"]
