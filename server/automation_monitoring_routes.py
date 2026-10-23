"""Local routes for monitoring customer-run automations from imported run reports."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import automation_monitoring as monitoring
from . import event_log_workspace as workspace
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "automation_monitoring.js"


def _require_dashboard(request: Request) -> None:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required for automation monitoring")


def _require_storage_consent(request: Request) -> None:
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store this on this computer is required")


async def _json(request: Request, limit: int) -> Any:
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, "request must be JSON")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, "request is larger than the supported limit")
    try:
        return json.loads(bytes(body))
    except ValueError:
        raise HTTPException(422, "request is not valid JSON") from None


async def _respond(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> JSONResponse:
    try:
        result = await run_in_threadpool(operation, *args, **kwargs)
    except (monitoring.MonitoringError, workspace.EventLogWorkspaceError) as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/automation-monitoring.js")
def automation_monitoring_script() -> FileResponse:
    """Static dashboard client; it only calls the authenticated routes below."""
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.get("/v1/automation-monitoring/deployments")
async def list_deployments(request: Request):
    _require_dashboard(request)
    return await _respond(monitoring.list_deployments)


@router.post("/v1/automation-monitoring/reports")
async def import_report(request: Request, deployment_id: str | None = Query(default=None, max_length=40)):
    _require_dashboard(request)
    _require_storage_consent(request)
    document = await _json(request, monitoring.MAX_REPORT_BYTES)
    return await _respond(monitoring.import_report, document, deployment_id or None)


@router.put("/v1/automation-monitoring/deployments/{deployment_id}/design")
async def attach_design(request: Request, deployment_id: str):
    _require_dashboard(request)
    spec = await _json(request, 1024 * 1024)
    return await _respond(monitoring.attach_design, deployment_id, spec)


@router.get("/v1/automation-monitoring/deployments/{deployment_id}/monitor")
async def monitor_deployment(request: Request, deployment_id: str, recent_days: int = Query(default=7, ge=1, le=90),
                             baseline_days: int = Query(default=28, ge=1, le=365)):
    _require_dashboard(request)
    return await _respond(monitoring.monitor, deployment_id, recent_days=recent_days, baseline_days=baseline_days)


@router.post("/v1/automation-monitoring/deployments/{deployment_id}/delete")
async def delete_deployment(request: Request, deployment_id: str):
    _require_dashboard(request)
    return await _respond(monitoring.delete_deployment, deployment_id)


def _to_event_log(deployment_id: str) -> dict[str, Any]:
    data = monitoring.runs_csv(deployment_id)
    info = monitoring.list_deployments()["deployments"]
    name = next((d["name"] for d in info if d["deployment_id"] == deployment_id), "Automation")
    return workspace.import_log(BytesIO(data), name=(name + " runs")[:120], source_format="csv", mapping={
        "case_column": "run", "activity_column": "step", "timestamp_column": "time", "resource_column": "status"})


@router.post("/v1/automation-monitoring/deployments/{deployment_id}/event-log")
async def deployment_event_log(request: Request, deployment_id: str):
    _require_dashboard(request)
    _require_storage_consent(request)
    return await _respond(_to_event_log, deployment_id)
