"""Dashboard-only atomic acknowledgement of explicitly selected incidents."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import automation_incident_batch as batch
from . import automation_incidents as incidents
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "automation_incident_batch.js"


def _require_dashboard(request):
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required for incident acknowledgement")


@router.get("/automation-incident-batch.js")
def incident_batch_script():
    return FileResponse(_SCRIPT, media_type="application/javascript")


async def _call(request, operation):
    from .event_log_routes import _json_body
    _require_dashboard(request)
    payload = await _json_body(request, "incident batch acknowledgement", strict=True, limit=65536)
    _require_dashboard(request)
    try:
        value = await run_in_threadpool(operation, payload)
    except incidents.IncidentError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.post("/v1/automation-monitoring/incidents/batch/preview")
async def preview(request: Request):
    return await _call(request, batch.preview)


@router.post("/v1/automation-monitoring/incidents/batch/acknowledge")
async def acknowledge(request: Request):
    return await _call(request, batch.acknowledge)
