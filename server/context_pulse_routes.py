from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .context_pulse import context_pulse
from .secure_app import app


@app.get("/v1/context-pulse")
def get_context_pulse(
    cursor: str | None = None,
    recent_limit: int = 100,
    finding_limit: int = 20,
    lookback_days: int = 30,
) -> dict[str, Any]:
    """Return an incremental factual update for a connected AI client.

    The endpoint is read-only. The caller owns the opaque cursor and passes the
    returned ``next_cursor`` back on the next check. AI disclosure/redaction is
    still enforced by the established Context boundary middleware.
    """
    try:
        return context_pulse(
            cursor=cursor,
            recent_limit=recent_limit,
            finding_limit=finding_limit,
            lookback_days=lookback_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["app", "get_context_pulse"]
