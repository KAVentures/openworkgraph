"""Local routes for monitoring customer-run automations from imported run reports."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import automation_incidents as incidents
from . import automation_monitoring as monitoring
from . import automation_release_inventory as release_inventory
from . import automation_report_channels as channels
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
    return await _respond(_import_and_triage, document, deployment_id or None)


def _import_and_triage(document: Any, deployment_id: str | None) -> dict[str, Any]:
    result = monitoring.import_report(document, deployment_id)
    result["incidents_updated"] = incidents.reconcile_deployment(result["deployment"]["deployment_id"])
    return result


@router.put("/v1/automation-monitoring/deployments/{deployment_id}/design")
async def attach_design(request: Request, deployment_id: str):
    _require_dashboard(request)
    spec = await _json(request, 1024 * 1024)
    return await _respond(monitoring.attach_design, deployment_id, spec)


def _strict_release_json(value: bytes) -> Any:
    def unique(pairs):
        out = {}
        for key, item in pairs:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = item
        return out

    def invalid(_):
        raise ValueError("nonfinite value")

    try:
        return json.loads(value, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Invalid release review JSON") from None


@router.get("/v1/automation-monitoring/deployments/{deployment_id}/release")
async def get_release_inventory(request: Request, deployment_id: str):
    _require_dashboard(request)
    return await _respond(release_inventory.detail, deployment_id)


@router.post("/v1/automation-monitoring/deployments/{deployment_id}/release")
async def save_release_inventory(request: Request, deployment_id: str):
    _require_dashboard(request)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "application/json required")
    body = await request.body()
    if len(body) > 8192:
        raise HTTPException(413, "Release review is larger than 8 KiB")
    data = _strict_release_json(body)
    try:
        result = await run_in_threadpool(release_inventory.record, deployment_id, data)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except release_inventory.ReleaseReviewError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except monitoring.MonitoringError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/v1/automation-monitoring/deployments/{deployment_id}/monitor")
async def monitor_deployment(request: Request, deployment_id: str, recent_days: int = Query(default=7, ge=1, le=90),
                             baseline_days: int = Query(default=28, ge=1, le=365)):
    _require_dashboard(request)
    return await _respond(monitoring.monitor, deployment_id, recent_days=recent_days, baseline_days=baseline_days)


@router.post("/v1/automation-monitoring/deployments/{deployment_id}/delete")
async def delete_deployment(request: Request, deployment_id: str):
    _require_dashboard(request)
    return await _respond(_delete_with_channels, deployment_id)


def _delete_with_channels(deployment_id: str) -> dict[str, Any]:
    result = monitoring.delete_deployment(deployment_id)
    result["report_channels_deleted"] = channels.forget_deployment(deployment_id)
    result["incidents_deleted"] = incidents.forget_deployment(deployment_id)
    return result


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


# ------------------------------------------------------------ signed report channels and incidents

def _channel_call(operation: Callable[..., Any], *args: Any) -> JSONResponse:
    try:
        return JSONResponse(operation(*args), headers={"Cache-Control": "no-store"})
    except channels.ChannelError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except incidents.IncidentError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/v1/automation-monitoring/deployments/{deployment_id}/channel")
async def create_report_channel(request: Request, deployment_id: str):
    _require_dashboard(request)
    return await run_in_threadpool(_channel_call, channels.create, deployment_id)


@router.get("/v1/automation-monitoring/channels")
async def list_report_channels(request: Request, deployment_id: str | None = Query(default=None, max_length=40)):
    _require_dashboard(request)
    return await run_in_threadpool(_channel_call, channels.listing, deployment_id)


@router.post("/v1/automation-monitoring/channels/{channel_id}/rotate")
async def rotate_report_channel(request: Request, channel_id: str):
    _require_dashboard(request)
    return await run_in_threadpool(_channel_call, channels.rotate, channel_id)


@router.post("/v1/automation-monitoring/channels/{channel_id}/revoke")
async def revoke_report_channel(request: Request, channel_id: str):
    _require_dashboard(request)
    return await run_in_threadpool(_channel_call, channels.revoke, channel_id)


@router.post("/v1/automation-monitoring/channels/{channel_id}/reports")
async def receive_signed_report(request: Request, channel_id: str):
    """For the automation itself: local API token plus the channel signature; no dashboard session."""
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, "request must be JSON")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > monitoring.MAX_REPORT_BYTES:
            raise HTTPException(413, "report is larger than the supported limit")
    return await run_in_threadpool(_channel_call, channels.ingest, channel_id,
                                   request.headers.get("x-owg-report-timestamp", ""),
                                   request.headers.get("x-owg-report-sequence", ""),
                                   request.headers.get("x-owg-report-signature", ""), bytes(body))


@router.get("/v1/automation-monitoring/incidents")
async def list_automation_incidents(request: Request,
                                    status: str | None = Query(default=None, pattern="^(open|acknowledged|resolved)$"),
                                    deployment_id: str | None = Query(default=None, max_length=40)):
    _require_dashboard(request)
    return await run_in_threadpool(_channel_call, incidents.listing, status, deployment_id)


@router.post("/v1/automation-monitoring/incidents/{incident_id}/triage")
async def triage_automation_incident(request: Request, incident_id: str):
    _require_dashboard(request)
    payload = await _json(request, 4096)
    return await run_in_threadpool(_channel_call, incidents.triage, incident_id, payload)
