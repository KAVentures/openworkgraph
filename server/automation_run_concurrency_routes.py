"""Dedicated retained run concurrency dashboard routes."""
from pathlib import Path
from fastapi import APIRouter,Request
from fastapi.responses import FileResponse
from . import automation_run_concurrency as concurrency

router=APIRouter()
_SCRIPT=Path(__file__).resolve().parents[1]/"dashboard"/"automation_run_concurrency.js"


@router.get("/automation-run-concurrency.js")
def script():
    return FileResponse(_SCRIPT,media_type="application/javascript",
                        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})


@router.post("/v1/automation-monitoring/deployments/{deployment_id}/run-concurrency")
async def review(request: Request,deployment_id: str):
    from .automation_monitoring_routes import _require_dashboard,_respond
    from .event_log_routes import _json_body
    _require_dashboard(request)
    spec=await _json_body(request,"run concurrency review",strict=True,limit=8192)
    _require_dashboard(request)
    response=await _respond(concurrency.review,deployment_id,spec)
    _require_dashboard(request)
    return response
