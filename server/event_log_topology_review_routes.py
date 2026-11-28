"""Dedicated dashboard-only OCEL topology review."""
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from . import event_log_topology_review as topology
from .event_log_routes import _json_body, _require_dashboard, _respond

router=APIRouter()
_DASH=Path(__file__).resolve().parents[1]/"dashboard"
_HEADERS={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}


@router.get("/object-topology-review")
def page():
    return FileResponse(_DASH/"object_topology_review.html",media_type="text/html",headers={
        **_HEADERS,"Content-Security-Policy":"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"})


@router.get("/object-topology-review.js")
def script():
    return FileResponse(_DASH/"object_topology_review.js",media_type="application/javascript",headers=_HEADERS)


@router.post("/v1/event-logs/{log_id}/object-relationships/topology")
async def review(request: Request,log_id: str):
    _require_dashboard(request)
    spec=await _json_body(request,"object topology review",strict=True,limit=8192)
    _require_dashboard(request)
    response=await run_in_threadpool(_respond,topology.review,log_id,spec)
    _require_dashboard(request)
    return response
