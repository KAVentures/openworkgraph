from __future__ import annotations

import re
from typing import Any

from .db import context_rows


def _limit(value: int) -> int:
    return max(1, min(int(value), 1000))


def search_context(
    query: str = "",
    *,
    surface: str | None = None,
    actor_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Search customer-owned work context with simple AND token matching.

    This deliberately stays deterministic/local. A future vector index can sit
    beside it without changing the API or the underlying event evidence.
    """
    clauses = ["1=1"]
    params: list[Any] = []
    tokens = [t for t in re.findall(r"[\w.@:/-]+", query.lower()) if len(t) >= 2][:12]
    for token in tokens:
        clauses.append("LOWER(context_text) LIKE ?")
        params.append(f"%{token}%")
    if surface:
        clauses.append("LOWER(surface) = ?")
        params.append(surface.lower())
    if actor_id:
        clauses.append("actor_id = ?")
        params.append(actor_id)
    params.append(_limit(limit))
    return context_rows(
        f"SELECT * FROM context_events WHERE {' AND '.join(clauses)} ORDER BY observed_at DESC LIMIT ?",
        tuple(params),
    )


def recent_context(limit: int = 100, *, actor_id: str | None = None) -> list[dict[str, Any]]:
    return search_context("", actor_id=actor_id, limit=limit)


def context_timeline(session_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    return context_rows(
        "SELECT * FROM context_events WHERE session_id = ? ORDER BY observed_at ASC LIMIT ?",
        (session_id, _limit(limit)),
    )
