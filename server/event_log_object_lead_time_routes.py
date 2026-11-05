"""Session-only routes for exact caller-supplied OCEL relationship lead times (R24)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from . import event_log_object_lead_times as lead_times
from .event_log_routes import _json_body, _require_dashboard, _respond

router = APIRouter()


@router.post("/v1/event-logs/{log_id}/object-relationships/lead-times")
async def object_relationship_lead_times(request: Request, log_id: str):
    # The standard event-log route guard requires an active local dashboard
    # session; neither a generic bearer nor an AI/MCP capability is sufficient.
    _require_dashboard(request)
    spec = await _json_body(request, "object relationship lead-time settings",
                            strict=True, limit=16_384)
    return await run_in_threadpool(_respond, lead_times.analyze, log_id, spec)


__all__ = ["router"]
