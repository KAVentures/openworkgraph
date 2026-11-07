"""Session-only routes for exact caller-supplied OCEL relationship lead times (R24)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import event_log_object_lead_times as lead_times
from .event_log_routes import _json_body, _require_dashboard, _respond

router = APIRouter()
_DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
             "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@router.get("/object-relationship-lead-times")
def object_relationship_lead_times_page():
    # HTML contains no customer data and no embedded credentials. Its client
    # must possess the real dashboard session before a protected API responds.
    return FileResponse(_DASHBOARD / "object_relationship_lead_times.html",
                        media_type="text/html", headers={**_HEADERS, "Content-Security-Policy": _PAGE_CSP})


@router.get("/object-relationship-lead-times.js")
def object_relationship_lead_times_script():
    return FileResponse(_DASHBOARD / "object_relationship_lead_times.js",
                        media_type="application/javascript", headers=_HEADERS)


@router.post("/v1/event-logs/{log_id}/object-relationships/lead-times")
async def object_relationship_lead_times(request: Request, log_id: str):
    # The standard event-log route guard requires an active local dashboard
    # session; neither a generic bearer nor an AI/MCP capability is sufficient.
    _require_dashboard(request)
    spec = await _json_body(request, "object relationship lead-time settings",
                            strict=True, limit=16_384)
    return await run_in_threadpool(_respond, lead_times.analyze, log_id, spec)


__all__ = ["router"]
