"""Local-only UI and API for supplied OCEL event-binding continuity."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import event_log_object_binding_continuity as bindings
from .event_log_routes import _json_body, _require_dashboard, _respond

router = APIRouter()
_DASH = Path(__file__).resolve().parents[1] / "dashboard"
_PAGE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@router.get("/object-binding-continuity")
def object_binding_continuity_page():
    return FileResponse(_DASH / "object_binding_continuity.html", media_type="text/html",
                        headers={**_PAGE_HEADERS, "Content-Security-Policy": _CSP})


@router.get("/object-binding-continuity.js")
def object_binding_continuity_script():
    return FileResponse(_DASH / "object_binding_continuity.js",
                        media_type="application/javascript", headers=_PAGE_HEADERS)


@router.post("/v1/event-logs/{log_id}/object-bindings/continuity")
async def object_binding_continuity(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object binding continuity settings",
                            strict=True, limit=16_384)
    return await run_in_threadpool(_respond, bindings.analyze, log_id, spec)


__all__ = ["router"]
