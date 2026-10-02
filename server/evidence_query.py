from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from . import db
from .dashboard_privacy_policy import safe_dashboard_action

_CURSOR_VERSION = 1
_MAX_LIMIT = 500
_MAX_QUERY = 200
_MAX_SURFACE = 160
_MAX_TITLE = 300
_MAX_TITLE_INPUT = 8_192


def _norm_filter(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _fingerprint(scope: str, surface: str, query: str, since: str | None, *, include_agents: bool = False) -> str:
    raw = json.dumps(
        {
            "scope": scope,
            "surface": surface.casefold(),
            "q": query.casefold(),
            "since": str(since or ""),
            "include_agents": bool(include_agents),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _b64encode(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> dict[str, Any]:
    try:
        padded = str(value) + "=" * (-len(str(value)) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid evidence cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid evidence cursor")
    return payload


def encode_cursor(*, observed_at: str, row_id: int, fingerprint: str, offset: int) -> str:
    return _b64encode(
        {
            "v": _CURSOR_VERSION,
            "ts": str(observed_at),
            "id": int(row_id),
            "fp": str(fingerprint),
            "offset": max(0, int(offset)),
        }
    )


def decode_cursor(cursor: str, *, fingerprint: str) -> tuple[str, int, int]:
    payload = _b64decode(cursor)
    if int(payload.get("v") or 0) != _CURSOR_VERSION:
        raise ValueError("Unsupported evidence cursor")
    if str(payload.get("fp") or "") != fingerprint:
        raise ValueError("Evidence cursor does not match the active filters")
    observed_at = str(payload.get("ts") or "")
    try:
        row_id = int(payload.get("id"))
        offset = int(payload.get("offset") or 0)
    except Exception as exc:
        raise ValueError("Invalid evidence cursor") from exc
    if not observed_at or row_id <= 0 or offset < 0:
        raise ValueError("Invalid evidence cursor")
    return observed_at, row_id, offset


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(
    *,
    since: str | None,
    surface: str,
    query: str,
    include_surface: bool = True,
    include_agents: bool = False,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_agents:
        # Agent runs have their own tab; this is the human evidence view.
        clauses.append("source != 'agent'")
    if since:
        clauses.append("observed_at >= ?")
        params.append(str(since))
    if include_surface and surface:
        clauses.append("LOWER(surface) = LOWER(?)")
        params.append(surface)
    if query:
        clauses.append("LOWER(context_text) LIKE ? ESCAPE '\\'")
        params.append("%" + _escape_like(query.casefold()) + "%")
    return clauses, params


def _sql_where(clauses: list[str]) -> str:
    return " WHERE " + " AND ".join(clauses) if clauses else ""


def _display_title(value: Any, cache: dict[str, str]) -> str:
    """The stored title with sensitive details tokenized, checked once more.

    Titles are protected before storage (browser_title_privacy): names, email
    addresses, phone numbers and personal identity numbers are already stable
    tokens. This runs the same protection again for display, so anything stored
    before that protection existed is covered too. Fails closed to "".
    """
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    # Do not truncate before privacy protection: cutting through an email,
    # identifier, or name can turn it into a fragment the detector no longer
    # recognises. Very large legacy values fail closed instead.
    if len(text) > _MAX_TITLE_INPUT:
        return ""
    if text not in cache:
        try:
            from browser_title_privacy import protect_text

            protected = protect_text(text)
            cache[text] = protected[:_MAX_TITLE] if isinstance(protected, str) else ""
        except Exception:
            cache[text] = ""
    return cache[text]


def _row_item(row: Any, cache: dict[str, str] | None = None) -> dict[str, Any]:
    """One evidence row for the human dashboard.

    The page or window title keeps its context (subjects, documents, projects,
    companies) with sensitive details shown as tokens, the same as exports and
    redacted AI context. URL paths and raw target labels are not sent: the
    action is a canonical semantic verb (or the structural event type).
    """
    value = dict(row)
    try:
        metadata = json.loads(value.get("metadata_json") or "{}")
    except Exception:
        metadata = {}
    event_type = str(metadata.get("event_type") or "") if isinstance(metadata, dict) else ""
    action = safe_dashboard_action(
        value.get("target_label"),
        value.get("action"),
        event_type=event_type,
    )
    surface = str(value.get("surface") or "Unknown")
    title = _display_title(value.get("resource_title"), {} if cache is None else cache)
    return {
        "event_id": str(value.get("event_id") or ""),
        "observed_at": str(value.get("observed_at") or ""),
        "surface": surface,
        "page": title or surface,
        "resource_title": title,
        "action": action,
        "source": str(value.get("source") or ""),
        "event_type": event_type,
    }


def query_evidence(
    *,
    scope: str,
    since: str | None,
    limit: int = 100,
    cursor: str | None = None,
    surface: str | None = None,
    q: str | None = None,
    include_agents: bool = False,
) -> dict[str, Any]:
    if scope not in {"current", "all"}:
        raise ValueError("scope must be current or all")
    page_limit = max(1, min(int(limit), _MAX_LIMIT))
    selected_surface = _norm_filter(surface, _MAX_SURFACE)
    query = _norm_filter(q, _MAX_QUERY)
    fingerprint = _fingerprint(
        scope,
        selected_surface,
        query,
        since,
        include_agents=include_agents,
    )
    cursor_ts = ""
    cursor_id = 0
    offset = 0
    if cursor:
        cursor_ts, cursor_id, offset = decode_cursor(cursor, fingerprint=fingerprint)

    base_clauses, base_params = _where(
        since=since,
        surface=selected_surface,
        query=query,
        include_surface=True,
        include_agents=include_agents,
    )
    page_clauses = list(base_clauses)
    page_params = list(base_params)
    if cursor_ts:
        page_clauses.append("(observed_at < ? OR (observed_at = ? AND id < ?))")
        page_params.extend([cursor_ts, cursor_ts, cursor_id])

    select_sql = (
        "SELECT id, event_id, observed_at, source, surface, action, resource_title, "
        "resource_locator, target_label, metadata_json FROM context_events"
        + _sql_where(page_clauses)
        + " ORDER BY observed_at DESC, id DESC LIMIT ?"
    )

    facet_clauses, facet_params = _where(
        since=since,
        surface="",
        query=query,
        include_surface=False,
        include_agents=include_agents,
    )

    with db.connect() as conn:
        rows = conn.execute(select_sql, (*page_params, page_limit + 1)).fetchall()
        total_row = conn.execute(
            "SELECT COUNT(*) AS n FROM context_events" + _sql_where(base_clauses),
            tuple(base_params),
        ).fetchone()
        facet_rows = conn.execute(
            "SELECT surface, COUNT(*) AS n FROM context_events"
            + _sql_where(facet_clauses)
            + " GROUP BY surface ORDER BY n DESC, surface ASC LIMIT 50",
            tuple(facet_params),
        ).fetchall()

    has_more = len(rows) > page_limit
    visible = rows[:page_limit]
    titles: dict[str, str] = {}
    items = [_row_item(row, titles) for row in visible]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = encode_cursor(
            observed_at=str(last["observed_at"]),
            row_id=int(last["id"]),
            fingerprint=fingerprint,
            offset=offset + len(visible),
        )

    count = len(items)
    total = int(total_row["n"] if total_row else 0)
    return {
        "scope": scope,
        "since": since,
        "surface": selected_surface or None,
        "q": query,
        "limit": page_limit,
        "items": items,
        "count": count,
        "total": total,
        "position_start": offset + 1 if count else 0,
        "position_end": offset + count,
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
        "surfaces": [
            {"surface": str(row["surface"] or "Unknown"), "count": int(row["n"] or 0)}
            for row in facet_rows
        ],
        "cursor_kind": "observed_at_id_keyset_v1",
        "source_layer": "privacy_hardened_context_events",
        "presentation_layer": "dashboard_sensitive_details_tokenized",
        "includes_agent_runs": include_agents,
    }
