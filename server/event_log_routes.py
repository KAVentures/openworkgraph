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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import event_log_analytics as analytics
from . import event_log_cases as case_pages
from . import event_log_cohorts as cohorts
from . import event_log_compliance as compliance
from . import event_log_dotted_chart as dotted
from . import event_log_drift as drift
from . import event_log_filter_sets as filter_sets
from . import event_log_map_comparison as map_comparison
from . import event_log_model_discovery as model_discovery
from . import process_discovery_provenance as discovery_provenance
from . import event_log_object_attributes as object_attributes
from . import event_log_object_constraints as object_constraints
from . import event_log_object_diagnostics as object_diagnostics
from . import event_log_objects as objects
from . import event_log_object_relationships as object_relationships
from . import event_log_organization as organization
from . import event_log_playback as playback
from . import event_log_process_map as process_maps
from . import event_log_predictions as predictions
from . import event_log_quality as quality
from . import event_log_quarantine as quarantine
from . import event_log_query as analysis
from . import analysis_snapshot_export
from . import event_log_signals as signals
from . import event_log_signal_inbox as signal_inbox
from . import event_log_simulation as simulation
from . import event_log_waiting as waiting
from . import event_log_workspace as workspace
from .db import DATA_DIR
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "event_log_workspace.js"
_TYPES = {"csv": "text/csv", "sourcecsv": "text/csv", "xes": "application/xml", "ocel": "application/json", "ocelxml": "application/xml"}
_SUFFIX = {"csv": "csv", "sourcecsv": "csv", "xes": "xes", "ocel": "jsonocel", "ocelxml": "xmlocel"}


def _source_identity_mapping(
    event_id_column: str = Query(default="", max_length=200),
    source_namespace: str = Query(default="", max_length=40),
    source_namespace_column: str = Query(default="", max_length=200),
    case_namespace: str = Query(default="", max_length=40),
    case_namespace_column: str = Query(default="", max_length=200),
    attributes_json_column: str = Query(default="", max_length=200),
) -> dict[str, str]:
    return {key: value for key, value in locals().items() if value}


def _require_dashboard(request: Request) -> None:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required for event logs")


def _respond(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> JSONResponse:
    try:
        return JSONResponse(operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None


def _case_filter(started_from: str = Query(default="", max_length=64),
                 started_before: str = Query(default="", max_length=64),
                 resource: str = Query(default="", max_length=200),
                 activity: str = Query(default="", max_length=200),
                 attribute: str = Query(default="", max_length=200),
                 follows: str = Query(default="", max_length=4000)) -> dict[str, Any]:
    """The case filter shared by analysis views; ``follows`` is a JSON list of follower rules."""
    cohort: dict[str, Any] = {"started_from": started_from, "started_before": started_before, "resource": resource,
                              "activity": activity, "attribute": attribute}
    if follows:
        try:
            cohort["follows"] = json.loads(follows)
        except ValueError:
            raise HTTPException(422, "follows must be a JSON list of rules") from None
    return cohort


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
    attribute_column: list[str] = Query(default=[], max_length=200),
    delimiter: str = Query(default=",", max_length=1),
    keep_rejected: bool = Query(default=False),
    naive_timestamp_offset: str = Query(default="", max_length=6),
    identity: dict = Depends(_source_identity_mapping),
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
                   "attribute_columns": attribute_column[:64],
                   "delimiter": "\t" if delimiter == "t" else delimiter,
                   **({"keep_rejected": True} if keep_rejected else {}),
                   **({"naive_timestamp_offset": naive_timestamp_offset} if naive_timestamp_offset else {})}
        mapping.update(identity)
        return _respond(workspace.import_log, spool, name=name, source_format=format, mapping=mapping)


@router.post("/v1/event-logs/{log_id}/append")
async def append_event_log(
    request: Request, log_id: str,
    format: str = Query(pattern="^(csv|xes|ocel|ocelxml)$"),
    case_column: str = Query(default="", max_length=200),
    activity_column: str = Query(default="", max_length=200),
    timestamp_column: str = Query(default="", max_length=200),
    resource_column: str = Query(default="", max_length=200),
    attribute_column: list[str] = Query(default=[], max_length=200),
    delimiter: str = Query(default=",", max_length=1),
    keep_rejected: bool = Query(default=False),
    naive_timestamp_offset: str = Query(default="", max_length=6),
    identity: dict = Depends(_source_identity_mapping),
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
                   "attribute_columns": attribute_column[:64],
                   "delimiter": "\t" if delimiter == "t" else delimiter,
                   **({"keep_rejected": True} if keep_rejected else {}),
                   **({"naive_timestamp_offset": naive_timestamp_offset} if naive_timestamp_offset else {})}
        mapping.update(identity)
        try:
            result = await run_in_threadpool(workspace.append_log, log_id, spool, source_format=format, mapping=mapping)
        except workspace.EventLogWorkspaceError as exc:
            raise HTTPException(422, str(exc)) from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/quality")
def event_log_quality(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(quality.quality, log_id)


@router.get("/v1/event-logs/{log_id}/quarantine")
def event_log_quarantine(request: Request, log_id: str, batch: int | None = Query(default=None, ge=0),
                         after: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
    _require_dashboard(request)
    return _respond(quarantine.quarantine, log_id, batch, after, limit)


@router.post("/v1/event-logs/{log_id}/quarantine/release")
async def event_log_quarantine_release(request: Request, log_id: str):
    _require_dashboard(request)
    body = await _json_body(request, "release")
    try:
        result = await run_in_threadpool(quarantine.release, log_id, body)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/quarantine/discard")
async def event_log_quarantine_discard(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(quarantine.discard, log_id, await _json_body(request, "discard"))


@router.get("/v1/event-logs/{log_id}/appends")
def event_log_appends(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.append_history, log_id)


@router.post("/v1/event-logs/{log_id}/delete")
def delete_event_log(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.delete_log, log_id)


@router.get("/v1/event-logs/{log_id}/summary")
def event_log_summary(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                      cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(workspace.summary, log_id, case_type, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/object-types")
def event_log_object_types(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.object_types, log_id)


@router.get("/v1/event-logs/{log_id}/object-map")
def event_log_object_map(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(objects.object_graph, log_id)


@router.get("/v1/event-logs/{log_id}/object-relationships")
def event_log_object_relationships(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(object_relationships.overview, log_id)


@router.post("/v1/event-logs/{log_id}/object-relationships/query")
async def event_log_object_relationship_query(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "relationship filters", strict=True)
    return await run_in_threadpool(_respond, object_relationships.query, log_id, spec)


@router.get("/v1/event-logs/{log_id}/object-flattening")
def event_log_object_flattening(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(object_diagnostics.flattening, log_id)


@router.get("/v1/event-logs/{log_id}/object-attributes")
def event_log_object_attributes(request: Request, log_id: str,
                                object_type: str | None = Query(default=None, max_length=200),
                                object_id: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    return _respond(object_attributes.attribute_changes, log_id, object_type, object_id)


@router.get("/v1/event-logs/{log_id}/variants")
def event_log_variants(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                       limit: int = Query(default=20, ge=1, le=200), cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(workspace.variants, log_id, case_type, limit, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/directly-follows")
def event_log_directly_follows(request: Request, log_id: str,
                               case_type: str | None = Query(default=None, max_length=200),
                               limit: int = Query(default=200, ge=1, le=2000)):
    _require_dashboard(request)
    return _respond(workspace.directly_follows, log_id, case_type, limit)


@router.get("/v1/event-logs/{log_id}/activities")
def event_log_activities(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                         cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(workspace.activities, log_id, case_type, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/kpis")
def event_log_kpis(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                   sla_seconds: int | None = Query(default=None, ge=1, le=10 ** 9),
                   automation_resources: str = Query(default="", max_length=10000),
                   automation_resource: list[str] | None = Query(default=None, max_length=50),
        cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    resources = (automation_resource if automation_resource is not None else
                 [item.strip() for item in automation_resources.split(",") if item.strip()])
    return _respond(analytics.kpis, log_id, case_type, sla_seconds, resources, cohort)


@router.get("/v1/event-logs/{log_id}/process-map")
def event_log_process_map(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                          activities: float = Query(default=1.0, gt=0, le=1), paths: float = Query(default=1.0, gt=0, le=1),
        cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(process_maps.process_map, log_id, case_type, activities=activities, paths=paths, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/playback")
def event_log_playback(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                       activities: float = Query(default=1.0, gt=0, le=1),
                       max_cases: int = Query(default=200, ge=1, le=playback.MAX_CASES),
                       cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(playback.playback, log_id, case_type, activities=activities, max_cases=max_cases, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/waiting-time")
def event_log_waiting_time(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                           mode: str = Query(default="auto", pattern="^(auto|lifecycle|end_attribute)$"),
                           lifecycle_attribute: str = Query(default="lifecycle:transition", min_length=1, max_length=120),
                           end_attribute: str = Query(default="", max_length=120),
        cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(waiting.waiting_time, log_id, case_type, mode=mode, lifecycle_attribute=lifecycle_attribute,
                    end_attribute=end_attribute, cohort=cohort)


@router.post('/v1/event-logs/{log_id}/waiting-time')
async def event_log_working_calendar(request: Request,log_id: str,
        case_type: str | None=Query(default=None,max_length=200),
        cohort: dict[str,Any]=Depends(_case_filter)):
    _require_dashboard(request)
    from pydantic import ValidationError
    from .event_log_calendar_metrics import WaitingCalendarRequest
    body=await _json_body(request,'working-calendar analysis',strict=True)
    try:spec=WaitingCalendarRequest.model_validate(body)
    except ValidationError:raise HTTPException(422,'Invalid working-calendar analysis settings') from None
    return await run_in_threadpool(_respond,waiting.waiting_time,log_id,case_type,
        cohort=cohort,**spec.model_dump())


@router.get("/v1/event-logs/{log_id}/dotted-chart")
def event_log_dotted_chart(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                           time: str = Query(default="absolute", pattern="^(absolute|relative)$"),
                           order: str = Query(default="start", pattern="^(start|duration)$"),
                           max_cases: int = Query(default=300, ge=1, le=dotted.MAX_CASES),
        cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(dotted.dotted_chart, log_id, case_type, time=time, order=order, max_cases=max_cases, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/drift")
def event_log_drift(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                    window: str = Query(default="month", pattern="^(week|month|cases)$"),
                    window_cases: int = Query(default=500, ge=20, le=100_000),
                    min_cases: int = Query(default=30, ge=10, le=10_000),
        cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(drift.drift, log_id, case_type, window=window, window_cases=window_cases, min_cases=min_cases,
                    cohort=cohort)


@router.get("/v1/event-logs/{log_id}/organization")
def event_log_organization(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                           names: str = Query(default="pseudonymous", max_length=20),
                           automation_resource: list[str] | None = Query(default=None, max_length=50),
                           limit: int = Query(default=100, ge=1, le=organization.MAX_EDGES),
                           cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(organization.organization, log_id, case_type, names=names, cohort=cohort,
                    automation_resources=automation_resource, limit=limit)


async def _json_body(request: Request, what: str, *, strict: bool = False) -> Any:
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, f"{what} must be JSON")
    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(413, f"{what} is too large")
    try:
        if strict:
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("Repeated JSON key")
                    result[key] = value
                return result
            def constant(_):
                raise ValueError("Non-finite JSON value")
            return json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, f"{what} is not valid JSON") from None


@router.post("/v1/event-logs/{log_id}/compliance")
async def event_log_compliance(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "compliance rules")
    try:
        result = await run_in_threadpool(compliance.check, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/object-constraints")
async def event_log_object_constraints(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object rules", strict=True)
    try:
        result = await run_in_threadpool(object_constraints.check, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/map-comparison")
async def event_log_map_comparison(request: Request, log_id: str,
                                   case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    spec = await _json_body(request, "map comparison")
    if not isinstance(spec, dict):
        raise HTTPException(422, "map comparison must be a JSON object")
    try:
        result = await run_in_threadpool(map_comparison.compare_maps, log_id, spec.get("cohorts"), case_type)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


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


@router.post("/v1/event-logs/{log_id}/case-sharing-preview")
async def event_log_case_sharing_preview(request: Request, log_id: str):
    _require_dashboard(request)
    if request.headers.get('x-owg-case-sharing-preview') != 'true':
        raise HTTPException(403,'Explicit case-level sharing preview confirmation is required')
    from .analysis_case_export import prepare
    parsed = await _json_body(request, 'analysis')
    return await run_in_threadpool(_respond, prepare, log_id, parsed)


@router.post("/v1/event-logs/{log_id}/analysis-sharing-preview")
async def analysis_sharing_preview(request: Request, log_id: str):
    _require_dashboard(request)
    if request.headers.get('x-owg-analysis-sharing-preview') != 'true':
        raise HTTPException(403, 'Explicit sharing-preview request required')
    spec = await _json_body(request, 'analysis sharing preview')
    try:
        result = await run_in_threadpool(analysis_snapshot_export.export_snapshot, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError:
        raise HTTPException(422, 'Analysis cannot fit the bounded sharing format; select fewer rows or measures') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


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


@router.get("/v1/event-logs/{log_id}/metrics")
def event_log_metrics(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(analysis.list_metrics, log_id)


@router.post("/v1/event-logs/{log_id}/metrics")
async def event_log_save_metric(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(analysis.save_metric, log_id, await _json_body(request, "case metric"))


@router.post("/v1/event-logs/{log_id}/metrics/{metric_id}/delete")
def event_log_delete_metric(request: Request, log_id: str, metric_id: str):
    _require_dashboard(request)
    return _respond(analysis.delete_metric, log_id, metric_id)


@router.get("/v1/event-logs/{log_id}/filter-sets")
def event_log_filter_sets(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(filter_sets.list_sets, log_id)


@router.post("/v1/event-logs/{log_id}/filter-sets")
async def event_log_save_filter_set(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(filter_sets.save, log_id, await _json_body(request, "filter set"))


@router.post("/v1/event-logs/{log_id}/filter-sets/{set_id}/delete")
def event_log_delete_filter_set(request: Request, log_id: str, set_id: str):
    _require_dashboard(request)
    return _respond(filter_sets.delete, log_id, set_id)


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


@router.get("/v1/event-logs/{log_id}/signal-inbox")
def event_log_signal_inbox(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(signal_inbox.list_inbox, log_id)


@router.post("/v1/event-logs/{log_id}/signal-inbox/{rule_id}/acknowledge")
async def event_log_signal_acknowledge(request: Request, log_id: str, rule_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "signal acknowledgement", strict=True)
    return _respond(signal_inbox.review, log_id, rule_id, spec, acknowledge=True)


@router.post("/v1/event-logs/{log_id}/signal-inbox/{rule_id}/reopen")
async def event_log_signal_reopen(request: Request, log_id: str, rule_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "signal review reopening", strict=True)
    return _respond(signal_inbox.review, log_id, rule_id, spec, acknowledge=False)


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


@router.get("/v1/event-logs/{log_id}/model-discovery")
def event_log_model_discovery(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                              noise: float = Query(default=0.2, ge=0, lt=1),
                              activities: float = Query(default=1.0, gt=0, le=1),
        cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(model_discovery.discover, log_id, case_type, noise=noise, activities=activities, cohort=cohort)


@router.post("/v1/event-logs/{log_id}/model-discovery-draft")
async def event_log_discovery_draft(request: Request, log_id: str):
    _require_dashboard(request)
    if request.headers.get('x-owg-local-storage-confirmed') != 'true':
        raise HTTPException(403, 'Confirm local retention of discovery snapshot metadata')
    return await run_in_threadpool(_respond, discovery_provenance.record_draft, log_id,
                                   await _json_body(request, 'discovery snapshot'))


@router.get('/v1/event-log-discovery/process-origin')
def process_discovery_origin(request: Request, workflow_id: str = Query(pattern=r'^[a-zA-Z0-9_-]{1,80}$'),
                             revision: int = Query(ge=1)):
    _require_dashboard(request)
    from .local_process_catalog import catalog
    try:
        record = catalog.get(workflow_id, revision)
    except LookupError:
        raise HTTPException(404, 'Process revision unavailable') from None
    return JSONResponse({'origins': discovery_provenance.origins(record, include_selection=True),
                         'execution_authorized': False, 'derivation_independently_verified': False},
                        headers={'Cache-Control': 'no-store'})


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
                                     max_paths: int = Query(default=10, ge=1, le=simulation.MAX_PATHS),
                                     routing: str = Query(default="paths", max_length=20)):
    _require_dashboard(request)
    return _respond(simulation.calibrate, log_id, case_type, max_paths, routing=routing)


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


@router.post("/v1/event-logs/{log_id}/simulation/validation")
async def event_log_simulation_validation(request: Request, log_id: str,
                                          case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    spec = await _json_body(request, "validation settings")
    try:
        result = await run_in_threadpool(simulation.validate, log_id, spec, case_type)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/export")
def export_event_log(request: Request, log_id: str, format: str = Query(pattern="^(csv|sourcecsv|xes|ocel|ocelxml)$"),
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

# Reuse this router's dashboard authentication and bounded JSON parser.
from .event_log_job_routes import add_routes as _add_analysis_job_routes
_add_analysis_job_routes(router, _require_dashboard, _json_body)
