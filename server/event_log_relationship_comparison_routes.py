"""Local dashboard-only retained relationship snapshot comparison."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import event_log_relationship_comparison as comparison
from .event_log_routes import _json_body, _require_dashboard, _respond

router = APIRouter()
from .event_log_topology_review_routes import router as topology_router
router.include_router(topology_router)
_DASH = Path(__file__).resolve().parents[1] / "dashboard"
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@router.get("/object-relationship-comparison")
def page():
    return FileResponse(_DASH / "object_relationship_comparison.html", media_type="text/html",
                        headers={**_HEADERS, "Content-Security-Policy":
                                 "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                                 "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"})


@router.get("/object-relationship-comparison.js")
def script():
    return FileResponse(_DASH / "object_relationship_comparison.js",
                        media_type="application/javascript", headers=_HEADERS)


@router.get("/v1/event-logs/{log_id}/object-relationships/snapshot")
def snapshot(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(comparison.pin, log_id)


@router.post("/v1/event-logs/{log_id}/object-relationships/compare")
async def compare(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "relationship snapshot comparison", strict=True, limit=16384)
    return await run_in_threadpool(_respond, comparison.compare, log_id, spec)
