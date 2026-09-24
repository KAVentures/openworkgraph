from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from . import db

_CURSOR_VERSION = 1
_MAX_LIMIT = 500
_MAX_QUERY = 200
_MAX_SURFACE = 160


def _norm_filter(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _fingerprint(scope: str, surface: str, query: str, since: str | None) -> str:
    raw = json.dumps(
        {
            "scope": scope,
            "surface": surface.casefold(),
            "q": query.casefold(),
            "since": str(since or ""),
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
) -> tuple[list[str], list[Any]]:
    """Build filters only over the content-minimized operational layer."""
    clauses: list[str] = []
    params: list[Any] = []
    if since:
        clauses.append("observed_at >= ?")
        params.append(str(since))
    if include_surface and surface:
        clauses.append("LOWER(app) = LOWER(?)")
        params.append(surface)
    if query:
        like = "%" + _escape_like(query.casefold()) + "%"
        clauses.append(
            "(LOWER(app) LIKE ? ESCAPE '\\' OR LOWER(window_title) LIKE ? ESCAPE '\\' "
            "OR LOWER(metadata_json) LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like])
    return clauses, params


def _sql_where(clauses: list[str]) -> str:
    return " WHERE " + " AND ".join(clauses) if clauses else ""


def _row_item(row: Any) -> dict[str, Any]:
    """Return only fields that are already safe in normalized_events.

    Arbitrary resource titles, URL paths and target labels intentionally never
    cross the localhost dashboard boundary. Rich context remains available in
    context_events for explicitly authorized AI workflows.
    """
    value = dict(row)
    try:
        metadata = json.loads(value.get("metadata_json") or "{}")
    except Exception:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    target = metadata.get("target") if isinstance(metadata.get("target"), dict) else {}
    page = metadata.get("page") if isinstance(metadata.get("page"), dict) else {}

    surface = str(value.get("app") or value.get("window_title") or page.get("surface") or "Unknown")
    event_type = str(value.get("event_type") or "")
    action = str(
        target.get("label")
        or target.get("title")
        or metadata.get("action")
        or event_type.replace("browser_", "").replace("screen_", "").replace("_", " ")
        or ""
    )
    page_label = str(page.get("surface") or page.get("title") or surface)

    return {
        "event_id": str(value.get("event_id") or ""),
        "observed_at": str(value.get("observed_at") or ""),
        "surface": surface,
        "page": page_label,
        "resource_title": "",
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
) -> dict[str, Any]:
    """Page human-facing evidence from normalized_events only.

    The localhost dashboard is intentionally stricter than customer-authorized
    context retrieval: it preserves work surfaces and semantic/structural actions
    while excluding arbitrary names, subjects, document titles and URL paths.
    """
    if scope not in {"current", "all"}:
        raise ValueError("scope must be current or all")
    page_limit = max(1, min(int(limit), _MAX_LIMIT))
    selected_surface = _norm_filter(surface, _MAX_SURFACE)
    query = _norm_filter(q, _MAX_QUERY)
    fingerprint = _fingerprint(scope, selected_surface, query, since)
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
    )
    page_clauses = list(base_clauses)
    page_params = list(base_params)
    if cursor_ts:
        page_clauses.append("(observed_at < ? OR (observed_at = ? AND id < ?))")
        page_params.extend([cursor_ts, cursor_ts, cursor_id])

    select_sql = (
        "SELECT id, event_id, observed_at, source, app, window_title, event_type, metadata_json "
        "FROM normalized_events"
        + _sql_where(page_clauses)
        + " ORDER BY observed_at DESC, id DESC LIMIT ?"
    )

    facet_clauses, facet_params = _where(
        since=since,
        surface="",
        query=query,
        include_surface=False,
    )

    with db.connect() as conn:
        rows = conn.execute(select_sql, (*page_params, page_limit + 1)).fetchall()
        total_row = conn.execute(
            "SELECT COUNT(*) AS n FROM normalized_events" + _sql_where(base_clauses),
            tuple(base_params),
        ).fetchone()
        facet_rows = conn.execute(
            "SELECT app AS surface, COUNT(*) AS n FROM normalized_events"
            + _sql_where(facet_clauses)
            + " GROUP BY app ORDER BY n DESC, surface ASC LIMIT 50",
            tuple(facet_params),
        ).fetchall()

    has_more = len(rows) > page_limit
    visible = rows[:page_limit]
    items = [_row_item(row) for row in visible]
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
        "source_layer": "operational_normalized",
    }
