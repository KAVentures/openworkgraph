from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from shared.evidence import RAW_RICH_EVIDENCE_CONTRACT, rich_evidence_row
from .db import connect

_REDACTED_TOKEN_RE = re.compile(r"^(?:PERSON|EMAIL|PHONE|PERSONNUMMER|ID)_[0-9A-F]{4,}$", re.I)


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


def _filter_clauses(
    *,
    snapshot_until: str,
    since: str | None,
    until: str | None,
    query: str | None,
    app_name: str | None,
    session_id: str | None,
) -> tuple[list[str], list[Any]]:
    clauses = ["observed_at <= ?"]
    params: list[Any] = [snapshot_until]
    if since:
        clauses.append("observed_at >= ?")
        params.append(since)
    if until:
        clauses.append("observed_at <= ?")
        params.append(until)
    if app_name:
        clauses.append("LOWER(COALESCE(app,'')) = LOWER(?)")
        params.append(app_name)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if query:
        like = f"%{query}%"
        clauses.append("(COALESCE(app,'') LIKE ? OR COALESCE(window_title,'') LIKE ? OR event_type LIKE ? OR metadata_json LIKE ?)")
        params.extend([like, like, like, like])
    return clauses, params


def _redacted_token_query(query: str | None) -> bool:
    """Whether query is an AI-visible pseudonym that does not exist at rest."""
    raw = str(query or "").strip()
    if not _REDACTED_TOKEN_RE.fullmatch(raw):
        return False
    try:
        from . import ai_context
        return (
            ai_context.ai_request_active()
            and ai_context.effective_detail()["detail_level"] == ai_context.DETAIL_REDACTED
        )
    except Exception:
        return False


def _matches_redacted_token(row: Any, token: str) -> bool:
    """Match an AI pseudonym without ever reversing it to a real identity."""
    try:
        from . import ai_context
        rich = rich_evidence_row(dict(row), include_identity=False)
        safe = ai_context.redact_contextually(rich, ai_context.user_settings())
        return token.casefold() in json.dumps(safe, ensure_ascii=False, sort_keys=True).casefold()
    except Exception:
        # Token-aware search is a privacy feature; on redaction failure do not
        # fall back to inspecting or returning literal identities.
        return False


def workflow_trace(
    *,
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    scope: str = "current",
    query: str | None = None,
    app_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return a stable chronological page of canonical rich local evidence.

    The event metadata is intentionally preserved so an AI can reconstruct work
    directly from observed evidence rather than being limited by OpenWorkGraph's
    current deterministic task inference. Internal actor/device/sensor IDs and
    local screenshot paths remain outside the local MCP trace.

    In Redacted AI mode, stable pseudonyms such as ``PERSON_ABC123`` are also
    valid queries. They are matched against the redacted representation locally;
    the real identity is never returned to or supplied by the caller.
    """
    page_limit = max(1, min(int(limit), 500))
    state = _decode_cursor(cursor or "") if cursor else {}

    if state:
        snapshot_until = str(state.get("snapshot_until") or "")
        effective_since = str(state.get("since") or "") or None
        effective_until = str(state.get("until") or "") or None
        effective_query = str(state.get("query") or "") or None
        effective_app = str(state.get("app_name") or "") or None
        effective_session = str(state.get("session_id") or "") or None
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
        effective_query = query
        effective_app = app_name
        effective_session = session_id
        after_at = None
        after_id = 0
        effective_scope = scope if scope in {"current", "all"} else "current"
        if effective_scope == "current" and not effective_since:
            effective_since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") or None

    token_query = _redacted_token_query(effective_query)
    clauses, params = _filter_clauses(
        snapshot_until=snapshot_until,
        since=effective_since,
        until=effective_until,
        query=None if token_query else effective_query,
        app_name=effective_app,
        session_id=effective_session,
    )

    if token_query:
        # Pseudonyms are presentation-time values, so SQL cannot match them.
        # Scan only the already-filtered frozen snapshot, redact each candidate
        # locally, then paginate the matching canonical rows by (time,id).
        with connect() as conn:
            candidates = conn.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY observed_at ASC, id ASC",
                tuple(params),
            ).fetchall()
        matches = [row for row in candidates if _matches_redacted_token(row, str(effective_query))]
        total = len(matches)
        if after_at:
            matches = [
                row for row in matches
                if str(row["observed_at"]) > after_at
                or (str(row["observed_at"]) == after_at and int(row["id"]) > after_id)
            ]
        has_more = len(matches) > page_limit
        visible = matches[:page_limit]
    else:
        count_clauses = list(clauses)
        count_params = list(params)
        if after_at:
            clauses.append("(observed_at > ? OR (observed_at = ? AND id > ?))")
            params.extend([after_at, after_at, after_id])

        with connect() as conn:
            total = int(conn.execute(
                f"SELECT COUNT(*) FROM events WHERE {' AND '.join(count_clauses)}",
                tuple(count_params),
            ).fetchone()[0])
            db_rows = conn.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY observed_at ASC, id ASC LIMIT ?",
                tuple(params + [page_limit + 1]),
            ).fetchall()
        has_more = len(db_rows) > page_limit
        visible = db_rows[:page_limit]

    rows = [rich_evidence_row(dict(row), include_identity=False) for row in visible]
    next_cursor = None
    if has_more and visible:
        last = dict(visible[-1])
        next_cursor = _encode_cursor({
            "snapshot_until": snapshot_until,
            "since": effective_since or "",
            "until": effective_until or "",
            "query": effective_query or "",
            "app_name": effective_app or "",
            "session_id": effective_session or "",
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
        "query_applied": bool(effective_query),
        "query_mode": "redacted_token" if token_query else ("stored_evidence" if effective_query else None),
        "app_filter": effective_app,
        "session_id": effective_session,
        "data_layer": "privacy_hardened_raw_rich_evidence",
        "evidence_contract": dict(RAW_RICH_EVIDENCE_CONTRACT),
        "derived_task_inference_authoritative": False,
    }
