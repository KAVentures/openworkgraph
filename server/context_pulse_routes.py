from __future__ import annotations

import base64
from datetime import datetime
import json
import os
from typing import Any

from fastapi import HTTPException, Request

from shared.history_policy import active_ai_history_access, history_generation
from .context_pulse import context_pulse
from .secure_app import app

_CURSOR_ENVELOPE_VERSION = 1


def _wrap_cursor(inner: str, generation: int) -> str:
    raw = json.dumps(
        {"h": _CURSOR_ENVELOPE_VERSION, "g": int(generation), "c": str(inner)},
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unwrap_cursor(value: str | None, generation: int) -> tuple[str | None, bool]:
    if not value:
        return None, False
    try:
        raw = base64.urlsafe_b64decode((str(value) + "=" * (-len(str(value)) % 4)).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or int(data.get("h") or 0) != _CURSOR_ENVELOPE_VERSION:
            return None, True
        if int(data.get("g") or 0) != int(generation):
            return None, True
        inner = str(data.get("c") or "")
        return (inner or None), False
    except Exception:
        # v0.93 and earlier cursors have no deletion-generation envelope. A
        # one-time bootstrap is safer than letting stale finding versions survive.
        return None, True


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed
    except Exception:
        return None


def _ai_history_filter(result: dict[str, Any], request: Request) -> None:
    if str(request.headers.get("X-OpenWorkGraph-Context") or "").strip().lower() != "ai":
        return
    access = active_ai_history_access()
    mode = str(access.get("mode") or "off")
    if mode == "all_saved":
        result["saved_history_access"] = "all_saved"
        return

    run_start = _parse(os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT"))
    selected_start = _parse(access.get("since")) if mode == "selected_range" else None
    selected_end = _parse(access.get("until")) if mode == "selected_range" else None

    def allowed(observed_at: Any) -> bool:
        observed = _parse(observed_at)
        if observed is None:
            return False
        if run_start is not None and observed >= run_start:
            return True
        return bool(
            selected_start is not None
            and selected_end is not None
            and selected_start <= observed < selected_end
        )

    rows = [row for row in list(result.get("recent_evidence") or []) if isinstance(row, dict) and allowed(row.get("observed_at"))]
    result["recent_evidence"] = rows
    result["recent_returned"] = len(rows)
    # Long-horizon findings aggregate multiple dates, so a selected range cannot
    # safely be enforced by filtering the final aggregate. Keep them off unless
    # the user granted all saved history; selected-range analysis should use
    # list_history + date-ranged canonical tools instead.
    result["findings"] = []
    result["findings_returned"] = 0
    result["findings_has_more"] = False
    result["saved_history_access"] = mode
    result["saved_history_findings_omitted"] = True


@app.get("/v1/context-pulse")
def get_context_pulse(
    request: Request,
    cursor: str | None = None,
    recent_limit: int = 12,
    finding_limit: int = 6,
    lookback_days: int = 30,
    recent_detail: str = "compact",
) -> dict[str, Any]:
    """Return an incremental factual update for a connected AI client.

    The outer cursor includes only a local history-generation number and the
    existing opaque Pulse cursor. Deletion/expiration invalidates it so deleted
    evidence cannot remain hidden in stale finding-version state.
    """
    generation = history_generation()
    inner, invalidated = _unwrap_cursor(cursor, generation)
    try:
        result = context_pulse(
            cursor=inner,
            recent_limit=recent_limit,
            finding_limit=finding_limit,
            lookback_days=lookback_days,
            recent_detail=recent_detail,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["next_cursor"] = _wrap_cursor(str(result.get("next_cursor") or ""), generation)
    result["history_generation"] = generation
    result["cursor_invalidated_by_history_change"] = bool(invalidated)
    if invalidated:
        result["cursor_notice"] = "Saved history changed; this Pulse restarted from a safe baseline."
    _ai_history_filter(result, request)
    return result


__all__ = ["app", "get_context_pulse"]
