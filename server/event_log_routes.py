"""Authenticated local routes for the large event-log workspace (CSV, XES, OCEL 2.0).

Every route requires a live dashboard session, exactly like retained process
logs: AI bearer tokens cannot read or change imported business logs. Importing
also requires an explicit local-storage confirmation header.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import event_log_analytics as analytics
from . import event_log_cases as case_pages
from . import event_log_cohorts as cohorts
from . import event_log_objects as objects
from . import event_log_organization as organization
from . import event_log_predictions as predictions
from . import event_log_query as analysis
from . import event_log_signals as signals
from . import event_log_simulation as simulation
from . import event_log_workspace as workspace
from .db import DATA_DIR
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "event_log_workspace.js"
_TYPES = {"csv": "text/csv", "xes": "application/xml", "ocel": "application/json", "ocelxml": "application/xml"}
_SUFFIX = {"csv": "csv", "xes": "xes", "ocel": "jsonocel", "ocelxml": "xmlocel"}


def _require_dashboard(request: Request) -> None:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required for event logs")


def _respond(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> JSONResponse:
    try:
        return JSONResponse(operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/event-log-workspace.js")
def event_log_workspace_script() -> FileResponse:
    """Static dashboard client; it only calls the authenticated routes below."""
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.get("/v1/event-logs")
def list_event_logs(request: Request):
    _require_dashboard(request)
    return _respond(workspace.list_logs)


@router.post("/v1/event-logs/import")
async def import_event_log(
    request: Request,
    format: str = Query(pattern="^(csv|xes|ocel|ocelxml)$"),
    name: str = Query(min_length=1, max_length=120),
    case_column: str = Query(default="", max_length=200),
    activity_column: str = Query(default="", max_length=200),
    timestamp_column: str = Query(default="", max_length=200),
    resource_column: str = Query(default="", max_length=200),
    delimiter: str = Query(default=",", max_length=1),
):
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store this event log on this computer is required")
    # Spool to disk as it arrives so multi-gigabyte logs never sit in memory.
    with tempfile.SpooledTemporaryFile(max_size=8 << 20, dir=str(DATA_DIR)) as spool:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > workspace.MAX_IMPORT_BYTES:
                raise HTTPException(413, "event log is larger than the supported limit")
            spool.write(chunk)
        spool.seek(0)
        mapping = {"case_column": case_column, "activity_column": activity_column,
                   "timestamp_column": timestamp_column, "resource_column": resource_column,
                   "delimiter": "\t" if delimiter == "t" else delimiter}
        return _respond(workspace.import_log, spool, name=name, source_format=format, mapping=mapping)


@router.post("/v1/event-logs/{log_id}/append")
async def append_event_log(
    request: Request, log_id: str,
    format: str = Query(pattern="^(csv|xes|ocel|ocelxml)$"),
    case_column: str = Query(default="", max_length=200),
    activity_column: str = Query(default="", max_length=200),
    timestamp_column: str = Query(default="", max_length=200),
    resource_column: str = Query(default="", max_length=200),
    delimiter: str = Query(default=",", max_length=1),
):
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store these events on this computer is required")
    with tempfile.SpooledTemporaryFile(max_size=8 << 20, dir=str(DATA_DIR)) as spool:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > workspace.MAX_IMPORT_BYTES:
                raise HTTPException(413, "event log is larger than the supported limit")
            spool.write(chunk)
        spool.seek(0)
        mapping = {"case_column": case_column, "activity_column": activity_column,
                   "timestamp_column": timestamp_column, "resource_column": resource_column,
                   "delimiter": "\t" if delimiter == "t" else delimiter}
        try:
            result = await run_in_threadpool(workspace.append_log, log_id, spool, source_format=format, mapping=mapping)
        except workspace.EventLogWorkspaceError as exc:
            raise HTTPException(422, str(exc)) from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/appends")
def event_log_appends(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.append_history, log_id)


@router.post("/v1/event-logs/{log_id}/delete")
def delete_event_log(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.delete_log, log_id)


@router.get("/v1/event-logs/{log_id}/summary")
def event_log_summary(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    return _respond(workspace.summary, log_id, case_type)


@router.get("/v1/event-logs/{log_id}/object-types")
def event_log_object_types(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.object_types, log_id)


@router.get("/v1/event-logs/{log_id}/object-map")
def event_log_object_map(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(objects.object_graph, log_id)


@router.get("/v1/event-logs/{log_id}/variants")
def event_log_variants(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                       limit: int = Query(default=20, ge=1, le=200)):
    _require_dashboard(request)
    return _respond(workspace.variants, log_id, case_type, limit)


@router.get("/v1/event-logs/{log_id}/directly-follows")
def event_log_directly_follows(request: Request, log_id: str,
                               case_type: str | None = Query(default=None, max_length=200),
                               limit: int = Query(default=200, ge=1, le=2000)):
    _require_dashboard(request)
    return _respond(workspace.directly_follows, log_id, case_type, limit)


@router.get("/v1/event-logs/{log_id}/activities")
def event_log_activities(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    return _respond(workspace.activities, log_id, case_type)


@router.get("/v1/event-logs/{log_id}/kpis")
def event_log_kpis(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                   sla_seconds: int | None = Query(default=None, ge=1, le=10 ** 9),
                   automation_resources: str = Query(default="", max_length=10000),
                   automation_resource: list[str] | None = Query(default=None, max_length=50),
                   started_from: str = Query(default="", max_length=64),
                   started_before: str = Query(default="", max_length=64),
                   resource: str = Query(default="", max_length=200),
                   activity: str = Query(default="", max_length=200),
                   attribute: str = Query(default="", max_length=200)):
    _require_dashboard(request)
    resources = (automation_resource if automation_resource is not None else
                 [item.strip() for item in automation_resources.split(",") if item.strip()])
    cohort = {"started_from": started_from, "started_before": started_before, "resource": resource,
              "activity": activity, "attribute": attribute}
    return _respond(analytics.kpis, log_id, case_type, sla_seconds, resources, cohort)


@router.get("/v1/event-logs/{log_id}/organization")
def event_log_organization(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                           names: str = Query(default="pseudonymous", max_length=20),
                           automation_resource: list[str] | None = Query(default=None, max_length=50),
                           started_from: str = Query(default="", max_length=64),
                           started_before: str = Query(default="", max_length=64),
                           resource: str = Query(default="", max_length=200),
                           activity: str = Query(default="", max_length=200),
                           attribute: str = Query(default="", max_length=200),
                           limit: int = Query(default=100, ge=1, le=organization.MAX_EDGES)):
    _require_dashboard(request)
    cohort = {"started_from": started_from, "started_before": started_before, "resource": resource,
              "activity": activity, "attribute": attribute}
    return _respond(organization.organization, log_id, case_type, names=names, cohort=cohort,
                    automation_resources=automation_resource, limit=limit)


async def _json_body(request: Request, what: str) -> Any:
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, f"{what} must be JSON")
    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(413, f"{what} is too large")
    try:
        return json.loads(body)
    except ValueError:
        raise HTTPException(422, f"{what} is not valid JSON") from None


@router.post("/v1/event-logs/{log_id}/predictions")
async def event_log_predictions(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "prediction settings")
    try:
        result = await run_in_threadpool(predictions.predict, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/analysis")
async def event_log_run_analysis(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "analysis")
    try:
        result = await run_in_threadpool(analysis.run, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/analyses")
def event_log_saved_analyses(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(analysis.list_saved, log_id)


@router.post("/v1/event-logs/{log_id}/analyses")
async def event_log_save_analysis(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(analysis.save, log_id, await _json_body(request, "saved analysis"))


@router.post("/v1/event-logs/{log_id}/analyses/{analysis_id}/delete")
def event_log_delete_analysis(request: Request, log_id: str, analysis_id: str):
    _require_dashboard(request)
    return _respond(analysis.delete_saved, log_id, analysis_id)


@router.get("/v1/event-logs/{log_id}/signals")
def event_log_signal_rules(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(signals.list_rules, log_id)


@router.post("/v1/event-logs/{log_id}/signals")
async def event_log_add_signal_rule(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(signals.add_rule, log_id, await _json_body(request, "signal rule"))


@router.post("/v1/event-logs/{log_id}/signals/evaluate")
def event_log_evaluate_signals(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(signals.evaluate, log_id)


@router.post("/v1/event-logs/{log_id}/signals/{rule_id}/delete")
def event_log_delete_signal_rule(request: Request, log_id: str, rule_id: str):
    _require_dashboard(request)
    return _respond(signals.delete_rule, log_id, rule_id)


@router.post("/v1/event-logs/{log_id}/benchmark")
async def event_log_benchmark(request: Request, log_id: str,
                              case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    spec = await _json_body(request, "benchmark")
    if not isinstance(spec, dict):
        raise HTTPException(422, "benchmark must be a JSON object")
    return _respond(cohorts.benchmark, log_id, spec.get("cohorts"), case_type, spec.get("sla_seconds"))


@router.post("/v1/event-logs/{log_id}/conformance")
async def event_log_conformance(request: Request, log_id: str,
                                case_type: str | None = Query(default=None, max_length=200),
                                max_variants: int = Query(default=500, ge=1, le=analytics.MAX_ALIGNED_VARIANTS)):
    _require_dashboard(request)
    model = await _json_body(request, "reference model")
    return _respond(analytics.conformance, log_id, model, case_type, max_variants)


@router.get("/v1/event-logs/{log_id}/root-cause")
def event_log_root_cause(request: Request, log_id: str, outcome: str = Query(max_length=40),
                         case_type: str | None = Query(default=None, max_length=200),
                         threshold_seconds: int | None = Query(default=None, ge=1, le=10 ** 9),
                         activity: str | None = Query(default=None, max_length=200),
                         limit: int = Query(default=30, ge=1, le=100)):
    _require_dashboard(request)
    return _respond(analytics.root_cause, log_id, outcome, case_type, threshold_seconds=threshold_seconds,
                    activity=activity, limit=limit)


@router.get("/v1/event-logs/{log_id}/simulation/calibration")
def event_log_simulation_calibration(request: Request, log_id: str,
                                     case_type: str | None = Query(default=None, max_length=200),
                                     max_paths: int = Query(default=10, ge=1, le=simulation.MAX_PATHS)):
    _require_dashboard(request)
    return _respond(simulation.calibrate, log_id, case_type, max_paths)


@router.post("/v1/event-logs/{log_id}/simulate")
async def event_log_simulate(request: Request, log_id: str,
                             case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    scenario = await _json_body(request, "scenario")
    # CPU-bound and bounded (at most MAX_CASES cases); run off the event loop.
    try:
        result = await run_in_threadpool(simulation.what_if, log_id, scenario, case_type)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/export")
def export_event_log(request: Request, log_id: str, format: str = Query(pattern="^(csv|xes|ocel|ocelxml)$"),
                     case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix="." + _SUFFIX[format],
                                         dir=str(DATA_DIR), delete=False)
    try:
        with handle:
            workspace.export_log(log_id, format, handle, case_type)
    except workspace.EventLogWorkspaceError as exc:
        os.unlink(handle.name)
        raise HTTPException(422, str(exc)) from None
    return FileResponse(handle.name, media_type=_TYPES[format], filename=f"{log_id}.{_SUFFIX[format]}",
                        headers={"Cache-Control": "no-store"}, background=BackgroundTask(os.unlink, handle.name))


@router.post("/v1/event-logs/{log_id}/cases/query")
async def event_log_cases(request: Request, log_id: str,
                          case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    spec = await _json_body(request, "case query")
    return _respond(case_pages.cases, log_id, spec, case_type)


@router.post("/v1/event-logs/{log_id}/case-events/query")
async def event_log_case_events(request: Request, log_id: str,
                                case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    spec = await _json_body(request, "case event query")
    return _respond(case_pages.events, log_id, spec, case_type)
