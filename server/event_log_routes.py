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
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .event_log_cohort_export import MatchingExportFormat, export_matching_cases
from . import event_log_analytics as analytics
from . import event_log_variant_coverage as variant_coverage
from . import event_log_duration_distribution as duration_distribution
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
from . import event_log_object_process_model as object_process_model
from . import event_log_impact as impact
from . import event_log_domain_packs as domain_packs
from . import event_log_object_diagnostics as object_diagnostics
from . import event_log_objects as objects
from . import event_log_object_relationships as object_relationships
from . import event_log_object_paths as object_paths
from . import event_log_organization as organization
from . import event_log_playback as playback
from . import event_log_process_map as process_maps
from . import event_log_predictions as predictions
from . import event_log_prediction_monitoring as prediction_monitoring
from . import event_log_quality as quality
from . import event_log_quality_pages as quality_pages
from . import event_log_quarantine as quarantine
from . import event_log_query as analysis
from . import event_log_linked_attribute_analysis as linked_attribute_analysis
from . import analysis_snapshot_export
from . import event_log_signals as signals
from . import event_log_signal_inbox as signal_inbox
from . import event_log_signal_transitions as signal_transitions
from . import event_log_signal_overview as signal_overview
from . import event_log_signal_timeline as signal_timeline
from . import event_log_simulation as simulation
from . import event_log_simulation_portfolio as simulation_portfolio
from . import event_log_trace_clusters as trace_clusters
from . import event_log_waiting as waiting
from . import event_log_workspace as workspace
from .db import DATA_DIR
from .local_auth import dashboard_session_valid

router = APIRouter()
XLSX_MAX_BYTES = 64 * 1024 * 1024
SQLITE_MAX_BYTES = 256 * 1024 * 1024
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "event_log_workspace.js"
_TYPES = {"csv": "text/csv", "sourcecsv": "text/csv", "sourcecsv-review": "text/csv", "csv-review": "text/csv", "xes": "application/xml", "xes-attributes": "application/xml", "ocel": "application/json", "ocelxml": "application/xml"}
_SUFFIX = {"csv": "csv", "sourcecsv": "csv", "sourcecsv-review": "review.csv", "csv-review": "review.csv", "xes": "xes", "xes-attributes": "xes", "ocel": "jsonocel", "ocelxml": "xmlocel"}


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
    except domain_packs.DomainPackConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None


def _case_filter(started_from: str = Query(default="", max_length=64),
                 started_before: str = Query(default="", max_length=64),
                 resource: str = Query(default="", max_length=200),
                 activity: str = Query(default="", max_length=200),
                 attribute: str = Query(default="", max_length=200),
                 event_attribute: str = Query(default="", max_length=200),
                 tag: str = Query(default="", max_length=60),
                 follows: str = Query(default="", max_length=4000),
                 attribute_range: str = Query(default="", max_length=400),
                 starts_with: str = Query(default="", max_length=200),
                 ends_with: str = Query(default="", max_length=200),
                 duration_min: float | None = Query(default=None, ge=0, le=10 ** 10),
                 duration_max: float | None = Query(default=None, ge=0, le=10 ** 10),
                 ended_from: str = Query(default="", max_length=64),
                 ended_before: str = Query(default="", max_length=64),
                 events_min: int | None = Query(default=None, ge=1, le=10 ** 9),
                 events_max: int | None = Query(default=None, ge=1, le=10 ** 9),
                 path_cases_min: int | None = Query(default=None, ge=1, le=10 ** 9),
                 path_cases_max: int | None = Query(default=None, ge=1, le=10 ** 9),
                 rework: bool | None = Query(default=None),
                 without_tag: str = Query(default="", max_length=60),
                 without_activity: str = Query(default="", max_length=200)) -> dict[str, Any]:
    """The case filter shared by analysis views; ``follows`` is a JSON list of follower rules.

    ``starts_with``/``ends_with`` keep cases whose first/last activity is the given one; ``duration_min``
    (inclusive) and ``duration_max`` (exclusive) bound the case duration in seconds. ``ended_from``/
    ``ended_before`` bound the last event's time; ``events_min``/``events_max`` the events per case and
    ``path_cases_min``/``path_cases_max`` how many cases share the case's exact path (all inclusive);
    ``rework`` keeps cases that repeat an activity (true) or that repeat none (false).
    """
    cohort: dict[str, Any] = {"started_from": started_from, "started_before": started_before, "resource": resource,
                              "activity": activity, "attribute": attribute, "event_attribute": event_attribute,
                              "tag": tag, "starts_with": starts_with, "ends_with": ends_with}
    if duration_min is not None or duration_max is not None:
        cohort["duration"] = {k: v for k, v in (("min", duration_min), ("max", duration_max)) if v is not None}
    cohort.update({"ended_from": ended_from, "ended_before": ended_before, "rework": rework, "without_tag": without_tag,
                   "without_activity": without_activity})
    for key, low, high in (("events", events_min, events_max), ("path_cases", path_cases_min, path_cases_max)):
        if low is not None or high is not None:
            cohort[key] = {k: v for k, v in (("min", low), ("max", high)) if v is not None}
    if follows:
        try:
            cohort["follows"] = json.loads(follows)
        except ValueError:
            raise HTTPException(422, "follows must be a JSON list of rules") from None
    if attribute_range:
        try:
            cohort["attribute_range"] = json.loads(attribute_range)
        except ValueError:
            raise HTTPException(422, "attribute_range must be a JSON object {name, min, max}") from None
    return cohort


@router.post("/v1/event-logs/{log_id}/private-map")
async def event_log_private_map(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200)):
    """A differentially private directly-follows map. POST: every call draws fresh noise and spends epsilon."""
    from . import event_log_private_map as private
    _require_dashboard(request)
    body = await _json_body(request, "private map settings")
    if not isinstance(body, dict) or set(body) - {"epsilon", "max_transitions_per_case", "threshold"}:
        raise HTTPException(422, "settings accept epsilon, max_transitions_per_case and threshold")
    return await run_in_threadpool(_respond, private.private_map, log_id, case_type,
                                   **{k: v for k, v in body.items() if v is not None})


@router.get("/v1/event-logs/{log_id}/cost-model")
def event_log_cost_model(request: Request, log_id: str):
    """The log's saved cost model, or null when none is saved."""
    from . import event_log_costs as cost_model
    _require_dashboard(request)
    return _respond(lambda: {"log_id": log_id, "saved": cost_model.saved_model(log_id)})


@router.post("/v1/event-logs/{log_id}/cost-model")
async def event_log_save_cost_model(request: Request, log_id: str):
    """Keep the analyst's prices with the log; the report then includes a cost section."""
    from . import event_log_costs as cost_model
    _require_dashboard(request)
    spec = await _json_body(request, "cost model", strict=True, limit=cost_model.MAX_REQUEST_BYTES)
    try:
        result = await run_in_threadpool(cost_model.save_model, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/cost-model/preview")
async def event_log_preview_cost_model(request: Request, log_id: str):
    from . import event_log_costs as cost_model
    _require_dashboard(request)
    spec = await _json_body(request, "cost model file", strict=True, limit=cost_model.MAX_REQUEST_BYTES)
    return await run_in_threadpool(_respond, cost_model.preview_model, log_id, spec)


@router.post("/v1/event-logs/{log_id}/cost-model/delete")
def event_log_delete_cost_model(request: Request, log_id: str):
    from . import event_log_costs as cost_model
    _require_dashboard(request)
    return _respond(cost_model.delete_model, log_id)


@router.post("/v1/event-logs/{log_id}/costs")
async def event_log_costs(request: Request, log_id: str):
    """Process cost from a user-supplied cost model: per case, activity, variant and resource."""
    from . import event_log_costs as cost_model
    _require_dashboard(request)
    spec = await _json_body(request, "cost model", strict=True, limit=cost_model.MAX_REQUEST_BYTES)
    try:
        result = await run_in_threadpool(cost_model.costs, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/costs/cases")
async def event_log_cost_cases(request: Request, log_id: str):
    from . import event_log_costs as cost_model
    _require_dashboard(request)
    spec = await _json_body(request, "case cost settings", strict=True, limit=cost_model.MAX_REQUEST_BYTES)
    return await run_in_threadpool(_respond, cost_model.case_costs, log_id, spec)


@router.post("/v1/event-logs/prepare-salesforce")
async def prepare_salesforce_export(request: Request):
    """Prepare a selected offline export without retaining it as an event log."""
    _require_dashboard(request)
    from csv import DictReader
    from hashlib import sha256
    from io import StringIO
    from itertools import islice
    from shared.salesforce_case_history import MAX_BYTES, project

    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "JSON export preparation required")
    raw = bytearray()
    async for chunk in request.stream():
        # JSON escapes may double the size; reserve room for selection metadata.
        if len(raw) + len(chunk) > MAX_BYTES * 2 + 16384:
            raise HTTPException(413, "Export preparation request too large")
        raw.extend(chunk)
    try:
        data = json.loads(raw)
        if (type(data) is not dict or set(data) != {"account", "cases", "csv", "confirm_processing"}
                or data["confirm_processing"] is not True or type(data["csv"]) is not str):
            raise ValueError()
        csv_text, report = await run_in_threadpool(project, data["csv"].encode("utf-8"),
                                                  account=data["account"], cases=data["cases"])
        # Use the same declared namespace for later manual exports of this account.
        # It is a declaration, not verified Salesforce organization identity.
        namespace = "sfexp_" + sha256(data["account"].encode("utf-8")).hexdigest()[:32]
        preview = list(islice(DictReader(StringIO(csv_text, newline="")), 20))
        return JSONResponse({"csv": csv_text, "report": report, "namespace": namespace,
                             "preview": preview, "preview_omitted": report["events"] - len(preview),
                             "persisted": False}, headers={"Cache-Control": "no-store"})
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "Check the selected CaseHistory export, case IDs and account namespace") from None
    finally:
        raw.clear()


@router.post("/v1/event-logs/comparison")
async def compare_event_logs(request: Request):
    """Compare two saved logs (or one log under two filters): KPIs, activities, paths and variants."""
    from . import event_log_log_comparison as log_comparison
    _require_dashboard(request)
    spec = await _json_body(request, "log comparison", strict=True, limit=32768)
    try:
        result = await run_in_threadpool(log_comparison.compare, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/event-log-workspace.js")
def event_log_workspace_script() -> FileResponse:
    """Static dashboard client; it only calls the authenticated routes below."""
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.get("/v1/event-logs")
def list_event_logs(request: Request):
    from . import event_log_expiry as expiry
    _require_dashboard(request)
    expiry.sweep()                       # logs past the owner's deletion date are deleted before listing
    return _respond(workspace.list_logs)


@router.get("/v1/event-logs/storage")
def event_log_storage(request: Request):
    """Disk used by the saved logs: the database file, free space, and each log's estimated share."""
    from . import event_log_storage as log_storage
    _require_dashboard(request)
    return _respond(log_storage.storage)


@router.get("/v1/event-logs/signal-overview")
def event_log_signal_overview(
    request: Request, status: str = Query(default="attention", max_length=20),
    limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0, le=2500),
):
    """Local dashboard-only, non-mutating review queue across saved logs."""
    _require_dashboard(request)
    return _respond(signal_overview.overview, status=status, limit=limit, offset=offset)


@router.get("/v1/event-logs/signal-timeline")
def event_log_signal_timeline(
    request: Request, transition: str = Query(default="all", max_length=20),
    limit: int = Query(default=100, ge=1, le=200), before: str = Query(default="", max_length=256),
):
    """Read-only cross-log transition journal; local dashboard session only."""
    _require_dashboard(request)
    return _respond(signal_timeline.timeline, transition=transition, limit=limit, before=before)


@router.post("/v1/event-logs/xlsx-to-csv")
async def convert_xlsx_event_log(request: Request, sheet: str | None = Query(default=None, max_length=200)):
    """Convert one worksheet of an .xlsx file to CSV for the ordinary CSV import. Nothing is stored."""
    from . import event_log_xlsx as xlsx
    _require_dashboard(request)
    with tempfile.SpooledTemporaryFile(max_size=8 << 20, dir=str(DATA_DIR)) as spool:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > XLSX_MAX_BYTES:
                raise HTTPException(413, "the workbook is larger than the supported limit")
            spool.write(chunk)
        spool.seek(0)
        try:
            result = await run_in_threadpool(xlsx.to_csv, spool, sheet)
        except xlsx.XlsxError as exc:
            raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/sqlite-to-csv")
async def convert_sqlite_event_log(request: Request, table: str | None = Query(default=None, max_length=200)):
    """Convert one table of a SQLite database file to CSV for the ordinary CSV import. Nothing is stored."""
    from . import event_log_sqlite as sqlite_import
    _require_dashboard(request)
    with tempfile.SpooledTemporaryFile(max_size=8 << 20, dir=str(DATA_DIR)) as spool:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > SQLITE_MAX_BYTES:
                raise HTTPException(413, "the database is larger than the supported limit")
            spool.write(chunk)
        spool.seek(0)
        try:
            result = await run_in_threadpool(sqlite_import.to_csv, spool, table, folder=DATA_DIR)
        except sqlite_import.SqliteError as exc:
            raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/mapping-matches")
async def event_log_mapping_matches(request: Request):
    """Earlier CSV column mappings that fit a file header (by column names only). Nothing is stored."""
    from . import event_log_mapping_reuse as reuse
    _require_dashboard(request)
    spec = await _json_body(request, "mapping match settings", strict=True, limit=262144)
    return await run_in_threadpool(_respond, reuse.matches, spec)


@router.post("/v1/event-logs/jsonl-to-csv")
async def convert_jsonl_event_log(request: Request):
    """Convert a JSON Lines file (one object per line) to CSV for the ordinary CSV import. Nothing is stored."""
    from . import event_log_jsonl as jsonl
    _require_dashboard(request)
    with tempfile.SpooledTemporaryFile(max_size=8 << 20, dir=str(DATA_DIR)) as spool:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > jsonl.MAX_BYTES:
                raise HTTPException(413, "the file is larger than the supported limit")
            spool.write(chunk)
        spool.seek(0)
        try:
            result = await run_in_threadpool(jsonl.to_csv, spool)
        except jsonl.JsonlError as exc:
            raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/parquet-to-csv")
async def convert_parquet_event_log(request: Request):
    """Convert a Parquet file to CSV for the ordinary CSV import. Nothing is stored."""
    from . import event_log_parquet as parquet
    _require_dashboard(request)
    with tempfile.SpooledTemporaryFile(max_size=8 << 20, dir=str(DATA_DIR)) as spool:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > parquet.MAX_BYTES:
                raise HTTPException(413, "the file is larger than the supported limit")
            spool.write(chunk)
        spool.seek(0)
        try:
            result = await run_in_threadpool(parquet.to_csv, spool, folder=DATA_DIR)
        except parquet.ParquetError as exc:
            raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/timestamp-formats")
async def detect_timestamp_formats(request: Request, column: str = Query(min_length=1, max_length=200),
                                   delimiter: str = Query(default=",", max_length=1)):
    """Which known timestamp formats read every value of one column, from the start of a CSV file (body,
    at most 256 KB). Nothing is stored."""
    from . import event_log_timestamp_formats as formats
    _require_dashboard(request)
    body = await request.body()
    if len(body) > 256 * 1024:
        raise HTTPException(413, "send at most the first 256 KB of the file")
    try:
        samples = formats.sample_column(body.decode("utf-8", errors="replace"), "\t" if delimiter == "t" else delimiter, column)
        result = await run_in_threadpool(formats.detect, samples)
    except formats.TimestampFormatError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/sample")
def import_sample_event_log(request: Request):
    """Store the synthetic order-to-cash sample (or return it if it is already saved)."""
    from . import event_log_sample as sample
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store this event log on this computer is required")
    return _respond(sample.import_sample)


@router.post("/v1/event-logs/import-agent-runs")
async def import_agent_runs(request: Request):
    """Agent mining: observed AI agent runs (structure only) become a new event log."""
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store this event log on this computer is required")
    spec = await _json_body(request, "agent-run import", strict=True, limit=4096)
    from . import event_log_agent_mining as agent_mining
    return await run_in_threadpool(_respond, agent_mining.import_agent_runs, spec)


@router.get("/v1/event-logs/{log_id}/agent-runs")
async def agent_runs_source(request: Request, log_id: str):
    _require_dashboard(request)
    from . import event_log_agent_mining as agent_mining
    return await run_in_threadpool(_respond, agent_mining.source, log_id)


@router.post("/v1/event-logs/{log_id}/agent-runs/refresh")
async def refresh_agent_runs(request: Request, log_id: str):
    """Append AI agent runs that finished since the log was made or last refreshed."""
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store this event log on this computer is required")
    from . import event_log_agent_mining as agent_mining
    return await run_in_threadpool(_respond, agent_mining.refresh, log_id)


@router.post("/v1/event-logs/import/preview")
async def preview_event_log_import(
    request: Request,
    partial: bool = Query(default=False),
    case_column: str = Query(default="", max_length=200),
    activity_column: str = Query(default="", max_length=200),
    timestamp_column: str = Query(default="", max_length=200),
    resource_column: str = Query(default="", max_length=200),
    attribute_column: list[str] = Query(default=[], max_length=200),
    delimiter: str = Query(default=",", max_length=1),
    keep_rejected: bool = Query(default=False),
    naive_timestamp_offset: str = Query(default="", max_length=6),
    timestamp_format: str = Query(default="", max_length=64),
    identity: dict = Depends(_source_identity_mapping),
):
    _require_dashboard(request)
    from . import event_log_import_preview as preview

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > preview.MAX_BYTES:
            raise HTTPException(413, "CSV preview exceeds its supported size")
        body.extend(chunk)
    mapping = {"case_column": case_column, "activity_column": activity_column,
               "timestamp_column": timestamp_column, "resource_column": resource_column,
               "attribute_columns": attribute_column[:64], "delimiter": "\t" if delimiter == "t" else delimiter,
               "keep_rejected": keep_rejected, "naive_timestamp_offset": naive_timestamp_offset,
               "timestamp_format": timestamp_format, **identity}
    return await run_in_threadpool(_respond, preview.preview_csv, bytes(body), mapping, partial=partial)


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
    timestamp_format: str = Query(default="", max_length=64),
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
                   **({"naive_timestamp_offset": naive_timestamp_offset} if naive_timestamp_offset else {}),
                   **({"timestamp_format": timestamp_format} if timestamp_format else {})}
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
    timestamp_format: str = Query(default="", max_length=64),
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
                   **({"naive_timestamp_offset": naive_timestamp_offset} if naive_timestamp_offset else {}),
                   **({"timestamp_format": timestamp_format} if timestamp_format else {})}
        mapping.update(identity)
        try:
            result = await run_in_threadpool(workspace.append_log, log_id, spool, source_format=format, mapping=mapping)
        except workspace.EventLogWorkspaceError as exc:
            raise HTTPException(422, str(exc)) from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/feeds")
def list_event_log_feeds(request: Request, log_id: str):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    return _respond(folder_feeds.feeds, log_id)


@router.post("/v1/event-logs/{log_id}/feeds")
async def create_event_log_feed(request: Request, log_id: str):
    """Point the log at a local folder whose new CSV exports are appended with the log's mapping."""
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    spec = await _json_body(request, "folder feed settings", strict=True, limit=8192)
    return await run_in_threadpool(_respond, folder_feeds.create, log_id, spec)


@router.get("/v1/event-logs/feeds/{feed_id}/mapping")
def review_event_log_feed_mapping(request: Request, feed_id: str):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    return _respond(folder_feeds.review_mapping, feed_id)


@router.post("/v1/event-logs/feeds/{feed_id}/mapping")
async def apply_event_log_feed_mapping(request: Request, feed_id: str):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to use the reviewed mapping is required")
    document = await _json_body(request, "reviewed folder feed mapping", strict=True, limit=1024)
    return await run_in_threadpool(_respond, folder_feeds.apply_mapping, feed_id, document)


@router.get("/v1/event-logs/feeds/{feed_id}/preview")
def preview_event_log_feed(request: Request, feed_id: str):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    return _respond(folder_feeds.preview, feed_id)


@router.post("/v1/event-logs/feeds/{feed_id}/run")
async def run_event_log_feed(request: Request, feed_id: str):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store these events on this computer is required")
    return await run_in_threadpool(_respond, folder_feeds.run, feed_id)


@router.post("/v1/event-logs/feeds/{feed_id}/append-file")
async def append_selected_event_log_feed_file(request: Request, feed_id: str):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store these events on this computer is required")
    spec = await _json_body(request, "selected folder feed file", strict=True, limit=8192)
    return await run_in_threadpool(_respond, folder_feeds.append_one, feed_id, spec)


@router.post("/v1/event-logs/feeds/{feed_id}/delete")
async def delete_event_log_feed(request: Request, feed_id: str):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    return await run_in_threadpool(_respond, folder_feeds.delete, feed_id)


@router.post("/v1/event-logs/feeds/{feed_id}/retry-failed")
async def retry_failed_event_log_feed_files(request: Request, feed_id: str):
    """Retry unchanged failed content; leave unseen files for an ordinary run."""
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store these events on this computer is required")
    return await run_in_threadpool(_respond, folder_feeds.run, feed_id, retry_failed=True)


@router.get("/v1/event-logs/feeds/{feed_id}/failures")
def list_event_log_feed_failures(request: Request, feed_id: str, after: str = Query(default="", max_length=64)):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    return _respond(folder_feeds.failures, feed_id, after=after)


@router.post("/v1/event-logs/feeds/{feed_id}/retry-failed/{sha256}")
async def retry_one_event_log_feed_file(request: Request, feed_id: str, sha256: str):
    from . import event_log_folder_feeds as folder_feeds
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store these events on this computer is required")
    return await run_in_threadpool(_respond, folder_feeds.run, feed_id, retry_failed=True, retry_hash=sha256)


@router.post("/v1/event-logs/{log_id}/case-table/export")
async def export_case_table(request: Request, log_id: str):
    from . import event_log_case_table as case_table

    _require_dashboard(request)
    spec = await _json_body(request, "case table export settings", strict=True, limit=32768)

    def prepare():
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", suffix=".csv",
                                             dir=str(DATA_DIR), delete=False)
        try:
            with handle:
                case_table.export(log_id, spec, handle)
        except BaseException:
            os.unlink(handle.name)
            raise
        return handle.name

    try:
        filename = await run_in_threadpool(prepare)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    suffix = "review.csv" if spec.get("spreadsheet_review") is True else "csv"
    return FileResponse(filename, media_type="text/csv", filename=f"{log_id}-case-table.{suffix}",
                        headers={"Cache-Control": "no-store"}, background=BackgroundTask(os.unlink, filename))


@router.post("/v1/event-logs/{log_id}/case-attributes")
async def add_case_attributes(
    request: Request, log_id: str,
    id_column: str = Query(min_length=1, max_length=200),
    column: list[str] = Query(default=[], max_length=200),
    delimiter: str = Query(default=",", max_length=1),
    object_type: str | None = Query(default=None, max_length=200),
    replace: bool = Query(default=False),
):
    """Join columns of a CSV case table onto the log's cases (body: the CSV)."""
    from . import event_log_case_attributes as case_attributes
    _require_dashboard(request)
    if request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store these values on this computer is required")
    # A closed file in a private folder: Windows does not let DuckDB open a file this process holds open.
    with tempfile.TemporaryDirectory(dir=str(DATA_DIR), prefix="owg-case-table-") as folder:
        table = Path(folder) / "case-table.csv"
        size = 0
        with table.open("wb") as spool:
            async for chunk in request.stream():
                size += len(chunk)
                if size > workspace.MAX_IMPORT_BYTES:
                    raise HTTPException(413, "the case table is larger than the supported limit")
                spool.write(chunk)
        try:
            result = await run_in_threadpool(case_attributes.add, log_id, table, id_column=id_column,
                                             columns=column[:64], delimiter="\t" if delimiter == "t" else delimiter,
                                             object_type=object_type, replace=replace)
        except workspace.EventLogWorkspaceError as exc:
            raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/case-attributes")
def list_case_attribute_uploads(request: Request, log_id: str):
    from . import event_log_case_attributes as case_attributes
    _require_dashboard(request)
    return _respond(case_attributes.uploads, log_id)


@router.post("/v1/event-logs/{log_id}/case-attributes/remove")
async def remove_case_attributes(request: Request, log_id: str):
    from . import event_log_case_attributes as case_attributes
    _require_dashboard(request)
    spec = await _json_body(request, "case attribute removal", strict=True, limit=16384)
    return await run_in_threadpool(_respond, case_attributes.remove, log_id, spec)


@router.post("/v1/event-logs/{log_id}/activity-labels/search")
async def event_log_activity_label_search(request: Request, log_id: str):
    """Search activity labels in the current case notion, never source events."""
    from . import event_log_activity_lookup as activity_lookup
    _require_dashboard(request)
    body = await _json_body(request, "activity label search", strict=True, limit=4096)
    if not isinstance(body, dict) or set(body) - {"search", "after", "case_type"} or "search" not in body:
        raise HTTPException(422, "activity label search needs search text")
    return await run_in_threadpool(_respond, activity_lookup.search, log_id, body["search"],
                                   body.get("after", ""), body.get("case_type"))


@router.post("/v1/event-logs/{log_id}/resource-labels/search")
async def event_log_resource_label_search(request: Request, log_id: str):
    from . import event_log_activity_lookup as lookup
    _require_dashboard(request)
    body = await _json_body(request, "resource label search", strict=True, limit=4096)
    if not isinstance(body, dict) or set(body) - {"search", "after", "case_type"} or "search" not in body:
        raise HTTPException(422, "resource label search needs search text")
    return await run_in_threadpool(_respond, lookup.resource_search, log_id, body["search"],
                                   body.get("after", ""), body.get("case_type"))


@router.get("/v1/event-logs/{log_id}/quality")
def event_log_quality(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(quality.quality, log_id)


@router.get("/v1/event-logs/{log_id}/quality/overview")
def event_log_quality_overview(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(quality.overview, log_id)


@router.get("/v1/event-logs/{log_id}/quality/attribute-coverage")
def event_log_quality_attribute_coverage(request: Request, log_id: str,
        offset: int = Query(default=0, ge=0, le=2_147_483_647),
        revision: int | None = Query(default=None, ge=0, le=2_147_483_647)):
    from . import event_log_quality_coverage
    _require_dashboard(request)
    return _respond(event_log_quality_coverage.page, log_id, offset, revision)


@router.get("/v1/event-logs/{log_id}/quality/batch-history")
def event_log_quality_batch_history(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(quality.batch_history, log_id)


@router.get("/v1/event-logs/{log_id}/quality/batch-history/page")
def event_log_quality_batch_history_page(request: Request, log_id: str,
        before: int | None = Query(default=None, ge=0, le=2_147_483_647),
        limit: int = Query(default=100, ge=1, le=100)):
    _require_dashboard(request)
    return _respond(quality_pages.page, log_id, before, limit)


@router.post("/v1/event-logs/{log_id}/export/matching-cases")
async def export_event_log_matching_cases(request: Request, log_id: str,
                                          format: MatchingExportFormat = Query(default="csv")):
    _require_dashboard(request)
    spec = await _json_body(request, "matching-case export settings", strict=True, limit=32768)
    suffix = "review.csv" if format == "sourcecsv-review" else _SUFFIX[format]

    def prepare():
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", suffix="." + suffix,
                                             dir=str(DATA_DIR), delete=False)
        try:
            with handle:
                export_matching_cases(log_id, spec, handle, export_format=format)
        except BaseException:
            os.unlink(handle.name)
            raise
        return handle.name

    try:
        filename = await run_in_threadpool(prepare)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return FileResponse(filename, media_type=_TYPES[format], filename=f"{log_id}-matching-cases.{suffix}",
                        headers={"Cache-Control": "no-store"}, background=BackgroundTask(os.unlink, filename))


def _anonymized(log_id: str, case_type: str | None, cohort: dict[str, Any], min_cases: int, time_mode: str,
                resource_mode: str, activity_mode: str, summary_only: bool) -> dict[str, Any]:
    from . import event_log_anonymized_export as anonymized
    try:
        return anonymized.anonymized_export(log_id, case_type, min_cases=min_cases, time_mode=time_mode,
                                            resource_mode=resource_mode, activity_mode=activity_mode,
                                            cohort=cohort, summary_only=summary_only)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/v1/event-logs/{log_id}/anonymized-export")
def event_log_anonymized_export_summary(request: Request, log_id: str,
                                        case_type: str | None = Query(default=None, max_length=200),
                                        min_cases: int = Query(default=5, ge=2, le=1000),
                                        time_mode: str = Query(default="relative", pattern="^(exact|day|relative|elapsed|sequence)$"),
                                        resource_mode: str = Query(default="drop", pattern="^(drop|pseudonymize)$"),
                                        activity_mode: str = Query(default="keep", pattern="^(keep|pseudonymize)$"),
                                        cohort: dict = Depends(_case_filter)):
    """What an anonymized export would keep and remove, without the data."""
    _require_dashboard(request)
    result = _anonymized(log_id, case_type, cohort, min_cases, time_mode, resource_mode, activity_mode, True)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/anonymized-export.csv")
def event_log_anonymized_export_csv(request: Request, log_id: str,
                                    case_type: str | None = Query(default=None, max_length=200),
                                    min_cases: int = Query(default=5, ge=2, le=1000),
                                    time_mode: str = Query(default="relative", pattern="^(exact|day|relative|elapsed|sequence)$"),
                                    resource_mode: str = Query(default="drop", pattern="^(drop|pseudonymize)$"),
                                    activity_mode: str = Query(default="keep", pattern="^(keep|pseudonymize)$"),
                                    cohort: dict = Depends(_case_filter)):
    """The anonymized log as CSV: rare paths suppressed, new case IDs, coarser time, no attributes. Not stored."""
    _require_dashboard(request)
    result = _anonymized(log_id, case_type, cohort, min_cases, time_mode, resource_mode, activity_mode, False)
    return Response(result["csv"], media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-anonymized.csv"',
        "X-OWG-Anonymized": f"cases={result['cases_kept']}; suppressed={result['cases_suppressed']}; "
                            f"min_cases={result['min_cases']}"})


@router.post("/v1/event-logs/combine")
async def combine_event_logs(request: Request):
    """Combine 2 to 10 saved logs into a new saved log; the sources are unchanged."""
    from . import event_log_combine as combined_logs
    _require_dashboard(request)
    spec = await _json_body(request, "combine settings", strict=True, limit=8192)
    try:
        result = await run_in_threadpool(combined_logs.combine, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/derive/preview")
async def preview_derived_event_log(request: Request, log_id: str):
    from . import event_log_derive as derived_logs
    _require_dashboard(request)
    spec = await _json_body(request, "derived log selection", strict=True, limit=32768)
    return await run_in_threadpool(_respond, derived_logs.preview, log_id, spec)



@router.post("/v1/event-logs/{log_id}/derive")
async def derive_event_log(request: Request, log_id: str):
    """Save the cases matching a filter as a new event log; the source log is unchanged."""
    from . import event_log_derive as derived_logs
    _require_dashboard(request)
    spec = await _json_body(request, "derived log settings", strict=True, limit=32768)
    try:
        result = await run_in_threadpool(derived_logs.derive, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/case-notes")
def event_log_case_notes(request: Request, log_id: str, case_id: str | None = Query(default=None, min_length=1, max_length=500)):
    """The owner's notes on cases of this log, newest first; ``case_id`` narrows them to one case."""
    from . import event_log_case_notes as case_notes
    _require_dashboard(request)
    return _respond(case_notes.notes, log_id, case_id)


@router.post("/v1/event-logs/{log_id}/case-notes/query")
async def event_log_query_case_notes(request: Request, log_id: str):
    from . import event_log_case_notes as case_notes
    _require_dashboard(request)
    spec = await _json_body(request, "case note search", strict=True, limit=4096)
    return await run_in_threadpool(_respond, case_notes.query, log_id, spec)


@router.post("/v1/event-logs/{log_id}/case-notes")
async def event_log_add_case_note(request: Request, log_id: str, compact: bool = Query(default=False)):
    from . import event_log_case_notes as case_notes
    _require_dashboard(request)
    spec = await _json_body(request, "case note", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(case_notes.add, log_id, spec, compact=compact)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/case-notes/{note_id}/delete")
async def event_log_delete_case_note(request: Request, log_id: str, note_id: str, compact: bool = Query(default=False)):
    from . import event_log_case_notes as case_notes
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(case_notes.remove, log_id, note_id, compact=compact)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/quarantine")
def event_log_quarantine(request: Request, log_id: str, batch: int | None = Query(default=None, ge=0),
                         after: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
    _require_dashboard(request)
    return _respond(quarantine.quarantine, log_id, batch, after, limit)


@router.get("/v1/event-logs/{log_id}/quarantine/batches")
def event_log_quarantine_batches(request: Request, log_id: str,
        after: int = Query(default=-1, ge=-1, le=2_147_483_647),
        limit: int = Query(default=100, ge=1, le=100)):
    _require_dashboard(request)
    return _respond(quarantine.batch_history, log_id, after, limit)


@router.get("/v1/event-logs/{log_id}/quarantine/decisions")
def event_log_quarantine_decisions(request: Request, log_id: str,
        offset: int = Query(default=0, ge=0, le=2_147_483_647),
        limit: int = Query(default=100, ge=1, le=100)):
    _require_dashboard(request)
    return _respond(quarantine.decision_history, log_id, offset, limit)


@router.post("/v1/event-logs/{log_id}/quarantine/preview")
async def event_log_quarantine_preview(request: Request, log_id: str):
    """Read-only, counts-only release preview under the existing local dashboard session."""
    _require_dashboard(request)
    body = await _json_body(request, "preview")
    try:
        result = await run_in_threadpool(quarantine.preview, log_id, body)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


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
    body = await _json_body(request, "discard")
    return await run_in_threadpool(_respond, quarantine.discard, log_id, body)


@router.get("/v1/event-logs/{log_id}/case-tags")
def event_log_case_tags(request: Request, log_id: str, tag: str | None = Query(default=None, max_length=60),
                        limit: int = Query(default=200, ge=1, le=1000),
                        offset: int = Query(default=0, ge=0, le=200000)):
    """The log's case tags with counts; with ``tag``, the tagged cases and their notes."""
    from . import event_log_case_tags as case_tags
    _require_dashboard(request)
    return _respond(case_tags.listing, log_id, tag, limit, offset)


@router.post("/v1/event-logs/{log_id}/case-tags")
async def event_log_add_case_tags(request: Request, log_id: str):
    """Tag cases by ID or by a case filter. Tags are the analyst's judgement; events are not changed."""
    from . import event_log_case_tags as case_tags
    _require_dashboard(request)
    spec = await _json_body(request, "case tags", strict=True, limit=262144)
    try:
        result = await run_in_threadpool(case_tags.add, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/case-tags/preview")
async def event_log_preview_case_tags(request: Request, log_id: str):
    from . import event_log_case_tags as case_tags
    _require_dashboard(request)
    spec = await _json_body(request, "case tag preview", strict=True, limit=262144)
    return await run_in_threadpool(_respond, case_tags.preview, log_id, spec)


@router.post("/v1/event-logs/{log_id}/case-tags/note")
async def event_log_edit_case_tag_note(request: Request, log_id: str):
    from . import event_log_case_tags as case_tags
    _require_dashboard(request)
    spec = await _json_body(request, "case tag note", strict=True, limit=16384)
    return await run_in_threadpool(_respond, case_tags.edit_note, log_id, spec)


@router.post("/v1/event-logs/{log_id}/case-tags/remove")
async def event_log_remove_case_tags(request: Request, log_id: str):
    from . import event_log_case_tags as case_tags
    _require_dashboard(request)
    spec = await _json_body(request, "case tags", strict=True, limit=262144)
    try:
        result = await run_in_threadpool(case_tags.remove, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/appends")
def event_log_appends(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.append_history, log_id)


@router.get("/v1/event-logs/{log_id}/expiry")
def event_log_expiry_status(request: Request, log_id: str):
    """The date after which this log is deleted automatically, or null."""
    from . import event_log_expiry as expiry
    _require_dashboard(request)
    return _respond(expiry.status, log_id)


@router.post("/v1/event-logs/{log_id}/expiry")
async def event_log_set_expiry(request: Request, log_id: str):
    """Body: {"delete_after": ISO time with an offset, or null to keep the log}."""
    from . import event_log_expiry as expiry
    _require_dashboard(request)
    spec = await _json_body(request, "deletion date", strict=True, limit=1024)
    if not isinstance(spec, dict) or set(spec) != {"delete_after"}:
        raise HTTPException(422, 'send {"delete_after": "2025-01-31T00:00:00Z"} or {"delete_after": null}')
    return _respond(expiry.set_expiry, log_id, spec["delete_after"])


@router.post("/v1/event-logs/{log_id}/delete")
def delete_event_log(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.delete_log, log_id)


@router.post("/v1/event-logs/{log_id}/time-patterns")
async def event_log_time_patterns(request: Request, log_id: str):
    """Events and case arrivals by local weekday and hour."""
    from . import event_log_time_patterns as time_patterns
    _require_dashboard(request)
    spec = await _json_body(request, "time-pattern settings", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(time_patterns.time_patterns, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/time-patterns/cases")
async def event_log_time_pattern_cases(request: Request, log_id: str):
    from . import event_log_time_patterns as time_patterns
    _require_dashboard(request)
    spec = await _json_body(request, "time-pattern case settings", strict=True, limit=16384)
    return await run_in_threadpool(_respond, time_patterns.pattern_cases, log_id, spec)


@router.post("/v1/event-logs/{log_id}/rename")
async def rename_event_log(request: Request, log_id: str):
    """Body: {"name": "..."}; the same name rule as at import."""
    _require_dashboard(request)
    spec = await _json_body(request, "new name", strict=True, limit=1024)
    if not isinstance(spec, dict) or set(spec) != {"name"}:
        raise HTTPException(422, 'send {"name": "..."}')
    return _respond(workspace.rename_log, log_id, spec["name"])


@router.get("/v1/event-logs/{log_id}/summary")
def event_log_summary(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                      cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(workspace.summary, log_id, case_type, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/case-attribute-values")
def event_log_case_attribute_values(request: Request, log_id: str,
                                    case_type: str | None = Query(default=None, max_length=200),
                                    search: str | None = Query(default=None, min_length=1, max_length=200)):
    """Case attribute names, numeric ranges and common values, to suggest case-filter inputs."""
    from . import event_log_case_attribute_values as case_attribute_values
    _require_dashboard(request)
    return _respond(case_attribute_values.values, log_id, case_type, search=search)


@router.post("/v1/event-logs/{log_id}/attribute-profile")
async def event_log_attribute_profile(request: Request, log_id: str):
    """Coverage, type, level and frequent values of every event attribute."""
    from . import event_log_attribute_profile as attribute_profile
    _require_dashboard(request)
    spec = await _json_body(request, "attribute profile settings", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(attribute_profile.profile, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/attribute-profile/cases")
async def event_log_attribute_profile_cases(request: Request, log_id: str,
                                            case_type: str | None = Query(default=None, max_length=200)):
    from . import event_log_attribute_profile as attribute_profile
    _require_dashboard(request)
    spec = await _json_body(request, "attribute case selection", strict=True)
    return await run_in_threadpool(_respond, attribute_profile.cases, log_id, spec, case_type)


@router.post("/v1/event-logs/{log_id}/insights")
async def event_log_insights(request: Request, log_id: str):
    """Ranked, descriptive pointers to where case time and repetition concentrate."""
    from . import event_log_insights as log_insights
    _require_dashboard(request)
    spec = await _json_body(request, "insight settings", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(log_insights.insights, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/change-markers")
def event_log_change_markers(request: Request, log_id: str):
    """The owner's dated notes on this log's timeline, oldest first."""
    from . import event_log_change_markers as change_markers
    _require_dashboard(request)
    return _respond(change_markers.markers, log_id)


@router.post("/v1/event-logs/{log_id}/change-markers")
async def event_log_add_change_marker(request: Request, log_id: str):
    """Body: {"marked_at": ISO time with an offset, "label": text, "note"?: text}."""
    from . import event_log_change_markers as change_markers
    _require_dashboard(request)
    spec = await _json_body(request, "change marker", strict=True, limit=4096)
    return _respond(change_markers.add, log_id, spec)


@router.post("/v1/event-logs/{log_id}/change-markers/{marker_id}/delete")
def event_log_remove_change_marker(request: Request, log_id: str, marker_id: str):
    from . import event_log_change_markers as change_markers
    _require_dashboard(request)
    if not marker_id.startswith("mark_") or len(marker_id) > 40:
        raise HTTPException(422, "unknown change marker")
    return _respond(change_markers.remove, log_id, marker_id)


@router.get("/v1/event-logs/{log_id}/object-types")
def event_log_object_types(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(workspace.object_types, log_id)


@router.get("/v1/event-logs/{log_id}/object-map")
def event_log_object_map(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(objects.object_graph, log_id)


@router.get("/v1/event-logs/{log_id}/object-relationships")
def event_log_object_relationships(request: Request, log_id: str,
        offset: int = Query(default=0, ge=0, le=2_147_483_647),
        revision: int | None = Query(default=None, ge=0, le=2_147_483_647)):
    _require_dashboard(request)
    return _respond(object_relationships.overview, log_id, offset, revision)


@router.post("/v1/event-logs/{log_id}/object-relationships/query")
async def event_log_object_relationship_query(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "relationship filters", strict=True)
    return await run_in_threadpool(_respond, object_relationships.query, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-relationships/trace")
async def event_log_object_relationship_trace(request: Request, log_id: str):
    """Explicit bounded local multi-hop navigation over supplied O2O links."""
    _require_dashboard(request)
    spec = await _json_body(request, "relationship trace", strict=True, limit=16384)
    return await run_in_threadpool(_respond, object_paths.trace, log_id, spec)


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


@router.post("/v1/event-logs/{log_id}/stuck-cases")
async def event_log_stuck_cases(request: Request, log_id: str):
    """Open cases idle much longer than usual after their latest activity."""
    from . import event_log_stuck as stuck
    _require_dashboard(request)
    spec = await _json_body(request, "stuck-case settings", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(stuck.stuck_cases, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/automation-candidates")
def event_log_automation_candidates(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                                    limit: int = Query(default=15, ge=1, le=50),
                                    cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_automation_candidates as automation
    _require_dashboard(request)
    return _respond(automation.candidates, log_id, case_type, limit=limit, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/decision-points")
def event_log_decision_points(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                              cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_decision_mining as decisions
    _require_dashboard(request)
    return _respond(decisions.decision_points, log_id, case_type, cohort=cohort)


@router.post("/v1/event-logs/{log_id}/decision-points/cases")
async def event_log_decision_branch_cases(request: Request, log_id: str,
                                          case_type: str | None = Query(default=None, max_length=200),
                                          cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_decision_mining as decisions
    _require_dashboard(request)
    selection = await _json_body(request, "decision branch settings", strict=True, limit=8192)
    return await run_in_threadpool(_respond, decisions.branch_cases, log_id, case_type,
                                   selection=selection, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/decision-rules")
def event_log_decision_rules(request: Request, log_id: str,
                             decision_activity: str = Query(min_length=1, max_length=200),
                             case_type: str | None = Query(default=None, max_length=200),
                             max_depth: int = Query(default=3, ge=1, le=4),
                             cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_decision_mining as decisions
    _require_dashboard(request)
    return _respond(decisions.analyse, log_id, decision_activity, case_type, max_depth=max_depth, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/temporal-profile")
def event_log_temporal_profile(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                               zeta: float = Query(default=3.0, ge=0.5, le=10),
                               min_occurrences: int = Query(default=5, ge=2, le=10_000),
                               baseline_cutoff: str | None = Query(default=None, max_length=64),
                               cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_temporal_profile as temporal
    _require_dashboard(request)
    return _respond(temporal.temporal_profile, log_id, case_type, zeta=zeta, min_occurrences=min_occurrences,
                    cohort=cohort, baseline_cutoff=baseline_cutoff)


@router.post("/v1/event-logs/{log_id}/temporal-profile/cases")
async def event_log_temporal_cases(request: Request, log_id: str,
        case_type: str | None = Query(default=None, max_length=200),
        zeta: float = Query(default=3.0, ge=0.5, le=10),
        min_occurrences: int = Query(default=5, ge=2, le=10_000),
        baseline_cutoff: str | None = Query(default=None, max_length=64),
        cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_temporal_profile as temporal
    _require_dashboard(request)
    spec = await _json_body(request, "temporal case page", strict=True, limit=16_384)
    if not isinstance(spec, dict) or set(spec) - {"source", "destination", "direction", "after", "limit"}:
        raise HTTPException(422, "invalid temporal case page settings")
    return await run_in_threadpool(_respond, temporal.temporal_cases, log_id, case_type, zeta=zeta,
                                   min_occurrences=min_occurrences, cohort=cohort,
                                   baseline_cutoff=baseline_cutoff, **spec)


@router.get("/v1/event-logs/{log_id}/heuristics-net")
def event_log_heuristics_net(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                             dependency_threshold: float = Query(default=0.5, ge=0, le=1),
                             and_threshold: float = Query(default=0.65, ge=0, le=1),
                             loop_two_threshold: float = Query(default=0.5, ge=0, le=1),
                             min_occurrences: int = Query(default=1, ge=1, le=1_000_000),
                             all_connected: bool = Query(default=True),
                             cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_heuristics_miner as heuristics
    _require_dashboard(request)
    return _respond(heuristics.heuristics_net, log_id, case_type, dependency_threshold=dependency_threshold,
                    and_threshold=and_threshold, loop_two_threshold=loop_two_threshold,
                    min_occurrences=min_occurrences, all_connected=all_connected,
                    cohort=cohort)


@router.post("/v1/event-logs/{log_id}/heuristics-net/cases")
async def event_log_heuristics_cases(request: Request, log_id: str,
                                     case_type: str | None = Query(default=None, max_length=200),
                                     cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_heuristics_miner as heuristics
    _require_dashboard(request)
    selection = await _json_body(request, "heuristics case settings", strict=True, limit=8192)
    return await run_in_threadpool(_respond, heuristics.evidence_cases, log_id, case_type,
                                   selection=selection, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/footprints")
def event_log_footprints(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                         noise: float = Query(default=0.2, ge=0, lt=1), activities: float = Query(default=1.0, gt=0, le=1),
                         cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_footprints as footprints
    _require_dashboard(request)
    return _respond(footprints.footprints, log_id, case_type, noise=noise, activities=activities, cohort=cohort)


@router.post("/v1/event-logs/{log_id}/footprints/cases")
async def event_log_footprint_cases(request: Request, log_id: str,
                                    case_type: str | None = Query(default=None, max_length=200),
                                    cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_footprints as footprints
    _require_dashboard(request)
    selection = await _json_body(request, "footprint case settings", strict=True, limit=65536)
    return await run_in_threadpool(_respond, footprints.footprint_cases, log_id, case_type,
                                   selection=selection, cohort=cohort)


@router.post("/v1/event-logs/{log_id}/petri-net-replay")
async def event_log_petri_net_replay(request: Request, log_id: str,
                                     case_type: str | None = Query(default=None, max_length=200),
                                     cohort: dict[str, Any] = Depends(_case_filter)):
    """Replay the log on a PNML Petri net sent as the request body (at most 2 MB). The net is not stored."""
    from . import event_log_petri_replay as petri
    _require_dashboard(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > petri.MAX_BYTES:
            raise HTTPException(413, "the Petri net file is larger than 2 MB")
    try:
        result = await run_in_threadpool(petri.replay, log_id, bytes(body), case_type, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/reference-nets")
def event_log_reference_nets(request: Request, log_id: str):
    """Reference nets saved with this log (names and sizes, not contents)."""
    from . import event_log_reference_nets as nets
    _require_dashboard(request)
    return _respond(nets.listing, log_id)


@router.post("/v1/event-logs/{log_id}/reference-nets")
async def event_log_save_reference_net(request: Request, log_id: str):
    """Save a PNML net under a name: {"name": …, "pnml": …}."""
    from . import event_log_reference_nets as nets
    _require_dashboard(request)
    spec = await _json_body(request, "reference net", strict=True, limit=2_300_000)
    if not isinstance(spec, dict) or set(spec) - {"name", "pnml"}:
        raise HTTPException(422, "send name and pnml")
    try:
        result = await run_in_threadpool(nets.save, log_id, spec.get("name"), spec.get("pnml"))
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/reference-nets/{net_id}.pnml")
def event_log_reference_net_pnml(request: Request, log_id: str, net_id: str):
    from . import event_log_reference_nets as nets
    _require_dashboard(request)
    try:
        name, xml = nets.get(log_id, net_id)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(404, str(exc)) from None
    return Response(xml, media_type="application/xml", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{net_id}.pnml"'})


@router.post("/v1/event-logs/{log_id}/reference-nets/{net_id}/delete")
def event_log_delete_reference_net(request: Request, log_id: str, net_id: str):
    from . import event_log_reference_nets as nets
    _require_dashboard(request)
    return _respond(nets.delete, log_id, net_id)


@router.post("/v1/event-logs/{log_id}/petri-net-alignments")
async def event_log_petri_net_alignments(request: Request, log_id: str,
                                         case_type: str | None = Query(default=None, max_length=200),
                                         cohort: dict[str, Any] = Depends(_case_filter)):
    """Optimal alignments of each variant on a PNML Petri net sent as the body (at most 2 MB). Not stored."""
    from . import event_log_alignments as aligned
    from . import event_log_petri_replay as petri
    _require_dashboard(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > petri.MAX_BYTES:
            raise HTTPException(413, "the Petri net file is larger than 2 MB")
    try:
        result = await run_in_threadpool(aligned.alignments, log_id, bytes(body), case_type, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/petri-net-alignments/cases.csv")
async def event_log_non_fitting_cases_csv(request: Request, log_id: str,
                                          case_type: str | None = Query(default=None, max_length=200),
                                          cohort: dict[str, Any] = Depends(_case_filter)):
    """Each case that does not fit the PNML net sent as the body, as spreadsheet-safe CSV. Not stored."""
    from . import event_log_alignments as aligned
    from . import event_log_petri_replay as petri
    _require_dashboard(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > petri.MAX_BYTES:
            raise HTTPException(413, "the Petri net file is larger than 2 MB")
    try:
        text, meta = await run_in_threadpool(aligned.non_fitting_cases_csv, log_id, bytes(body), case_type,
                                             cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(text, media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-non-fitting-cases.csv"',
        "X-OWG-Non-Fitting": f"cases={meta['non_fitting_cases']}; unaligned={meta['unaligned_cases']}; "
                             f"truncated={str(meta['truncated']).lower()}"})


@router.post("/v1/event-logs/{log_id}/petri-net-alignments/tag")
async def event_log_tag_non_fitting_cases(request: Request, log_id: str,
                                          new_tag: str = Query(min_length=1, max_length=60),
                                          note: str = Query(default="", max_length=500),
                                          case_type: str | None = Query(default=None, max_length=200),
                                          cohort: dict[str, Any] = Depends(_case_filter)):
    """Tag every case that does not fit the PNML net sent as the body (within the case filter).

    ``new_tag`` names the tag to add; ``tag`` is the case filter's own parameter (cases already tagged)."""
    from . import event_log_alignments as aligned
    from . import event_log_petri_replay as petri
    _require_dashboard(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > petri.MAX_BYTES:
            raise HTTPException(413, "the Petri net file is larger than 2 MB")
    try:
        result = await run_in_threadpool(aligned.tag_non_fitting_cases, log_id, bytes(body), new_tag, case_type,
                                         note=note, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/model-quality")
def event_log_model_quality(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                            levels: str | None = Query(default=None, max_length=100),
                            activities: float = Query(default=1.0, gt=0, le=1),
                            include_nets: bool = False,
                            cohort: dict = Depends(_case_filter)):
    """Fitness, precision, generalization and simplicity of the discovered net at several noise levels."""
    from . import event_log_model_quality as quality
    _require_dashboard(request)
    return _respond(quality.model_quality, log_id, case_type, levels=levels, activities=activities, cohort=cohort,
                    include_nets=include_nets)


@router.get("/v1/event-logs/{log_id}/funnel")
def event_log_funnel(request: Request, log_id: str, step: list[str] = Query(default=[], max_length=500),
                     within_hours: float | None = Query(default=None, gt=0),
                     evidence_step: int | None = Query(default=None, ge=1, le=8),
                     evidence_status: str = Query(default="reached", pattern="^(reached|dropped)$"),
                     case_type: str | None = Query(default=None, max_length=200),
                     cohort: dict = Depends(_case_filter)):
    """How many cases reach each chosen step in order, the time between steps and where the rest stopped."""
    from . import event_log_funnel as funnel
    _require_dashboard(request)
    return _respond(funnel.funnel, log_id, step, case_type, within_hours=within_hours, cohort=cohort,
                    evidence_step=evidence_step, evidence_status=evidence_status)


@router.get("/v1/event-logs/{log_id}/unusual-cases")
def event_log_unusual_cases(request: Request, log_id: str, limit: int = Query(default=25, ge=1, le=200),
                            offset: int = Query(default=0, ge=0, le=10_000_000),
                            signal: str | None = Query(default=None, max_length=32),
                            case_type: str | None = Query(default=None, max_length=200),
                            cohort: dict = Depends(_case_filter)):
    """Cases that stand out (rare path, unusual duration or length, heavy rework, rare activity) and why."""
    from . import event_log_unusual_cases as unusual
    _require_dashboard(request)
    return _respond(unusual.unusual_cases, log_id, case_type, limit=limit, cohort=cohort, signal=signal, offset=offset)


@router.get("/v1/event-logs/{log_id}/due-dates")
def event_log_due_dates(request: Request, log_id: str, due_activity: str = Query(min_length=1, max_length=500),
                        due_attribute: str | None = Query(default=None, max_length=200),
                        due_days: float | None = Query(default=None, gt=0), early_days: float | None = Query(default=None, gt=0),
                        case_type: str | None = Query(default=None, max_length=200),
                        as_of: str | None = Query(default=None, max_length=64),
                        cohort: dict = Depends(_case_filter)):
    """Whether the chosen activity happened by each case's due date: on time, early, late or still open.

    The activity is ``due_activity``: ``activity`` is the case filter (cases containing an activity)."""
    from . import event_log_due_dates as due
    _require_dashboard(request)
    return _respond(due.due_dates, log_id, due_activity, case_type, due_attribute=due_attribute, due_days=due_days,
                    early_days=early_days, cohort=cohort, as_of=as_of)


@router.get("/v1/event-logs/{log_id}/due-dates/cases")
def event_log_due_cases(request: Request, log_id: str, due_activity: str = Query(min_length=1, max_length=500),
                        due_group: str = Query(min_length=1, max_length=32),
                        due_attribute: str | None = Query(default=None, max_length=200),
                        due_days: float | None = Query(default=None, gt=0), early_days: float | None = Query(default=None, gt=0),
                        case_type: str | None = Query(default=None, max_length=200),
                        as_of: str | None = Query(default=None, max_length=64),
                        after: str | None = Query(default=None, min_length=1, max_length=200),
                        limit: int = Query(default=50, ge=1, le=100), cohort: dict = Depends(_case_filter)):
    from . import event_log_due_dates as due
    _require_dashboard(request)
    return _respond(due.due_cases, log_id, due_activity, case_type, group=due_group, after=after, limit=limit,
                    due_attribute=due_attribute, due_days=due_days, early_days=early_days, cohort=cohort, as_of=as_of)


@router.get("/v1/event-logs/{log_id}/next-steps")
def event_log_next_steps(request: Request, log_id: str, step: list[str] = Query(default=[], max_length=500),
                         anchor: str = Query(default="start", max_length=20),
                         anchor_steps: int | None = Query(default=None, ge=1, le=30),
                         case_type: str | None = Query(default=None, max_length=200),
                         cohort: dict = Depends(_case_filter)):
    """After a sequence of steps (from the case start or anywhere), which activities come next and how fast."""
    from . import event_log_path_explorer as explorer
    _require_dashboard(request)
    return _respond(explorer.next_steps, log_id, step, case_type, anchor=anchor, cohort=cohort,
                    anchor_steps=anchor_steps)


@router.post("/v1/event-logs/{log_id}/next-steps/cases")
async def event_log_next_step_cases(request: Request, log_id: str, step: list[str] = Query(default=[], max_length=500),
                                    anchor: str = Query(default="start", max_length=20),
                                    anchor_steps: int | None = Query(default=None, ge=1, le=30),
                                    case_type: str | None = Query(default=None, max_length=200),
                                    cohort: dict = Depends(_case_filter)):
    from . import event_log_path_explorer as explorer
    _require_dashboard(request)
    selection = await _json_body(request, "path branch settings", strict=True, limit=8192)
    return await run_in_threadpool(_respond, explorer.branch_cases, log_id, step, case_type,
                                   selection=selection, anchor=anchor, anchor_steps=anchor_steps, cohort=cohort)


@router.post("/v1/event-logs/{log_id}/petri-net-deviation-causes")
async def event_log_petri_net_deviation_causes(request: Request, log_id: str,
                                               case_type: str | None = Query(default=None, max_length=200),
                                               cohort: dict[str, Any] = Depends(_case_filter)):
    """Case features associated with deviating from a PNML net sent as the body (at most 2 MB). Not stored."""
    from . import event_log_alignments as aligned
    from . import event_log_petri_replay as petri
    _require_dashboard(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > petri.MAX_BYTES:
            raise HTTPException(413, "the Petri net file is larger than 2 MB")
    try:
        result = await run_in_threadpool(aligned.deviation_causes, log_id, bytes(body), case_type, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/case-alignment")
def event_log_case_alignment(request: Request, log_id: str, case_id: str = Query(min_length=1, max_length=500),
                             noise: float = Query(default=0.2, ge=0, lt=1),
                             case_type: str | None = Query(default=None, max_length=200)):
    """How one case departs from the discovered model: its optimal alignment, extra and skipped steps."""
    from . import event_log_alignments as aligned
    _require_dashboard(request)
    return _respond(aligned.case_alignment, log_id, case_id, case_type, noise=noise)


@router.get("/v1/event-logs/{log_id}/model-change")
def event_log_model_change(request: Request, log_id: str, split: str | None = Query(default=None, max_length=64),
                           noise: float = Query(default=0.2, ge=0, lt=1),
                           case_type: str | None = Query(default=None, max_length=200),
                           cohort: dict = Depends(_case_filter)):
    """How the discovered model differs before and after a date: orderings, activities and cross-fit."""
    from . import event_log_model_change as change
    _require_dashboard(request)
    return _respond(change.model_change, log_id, case_type, split=split, noise=noise, cohort=cohort,
                    include_case_evidence=True)


@router.get("/v1/event-logs/{log_id}/model-comparison")
def event_log_model_comparison(request: Request, log_id: str, group_a: str = Query(min_length=3, max_length=400),
                               group_b: str = Query(min_length=3, max_length=400),
                               noise: float = Query(default=0.2, ge=0, lt=1),
                               case_type: str | None = Query(default=None, max_length=200),
                               cohort: dict = Depends(_case_filter)):
    """The discovered models of two groups of cases (case attribute values) compared."""
    from . import event_log_model_change as change
    _require_dashboard(request)
    return _respond(change.model_comparison, log_id, group_a, group_b, case_type, noise=noise, cohort=cohort,
                    include_case_evidence=True)


@router.get("/v1/event-logs/{log_id}/story")
def event_log_story(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                    cohort: dict = Depends(_case_filter)):
    """The process in plain words: sentences filled from computed figures, each linked to its analysis."""
    from . import event_log_story as story
    _require_dashboard(request)
    return _respond(story.story, log_id, case_type, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/case-compare")
def event_log_case_compare(request: Request, log_id: str, case_a: str = Query(min_length=1, max_length=500),
                           case_b: str = Query(min_length=1, max_length=500),
                           case_type: str | None = Query(default=None, max_length=200)):
    """Two cases side by side: shared steps, steps only one has, and where the second fell behind."""
    from . import event_log_case_compare as compare
    _require_dashboard(request)
    return _respond(compare.compare_cases, log_id, case_a, case_b, case_type)


@router.get("/v1/event-logs/{log_id}/similar-cases")
def event_log_similar_cases(request: Request, log_id: str, case_id: str = Query(min_length=1, max_length=500),
                            limit: int = Query(default=10, ge=1, le=50),
                            case_type: str | None = Query(default=None, max_length=200),
                            cohort: dict = Depends(_case_filter)):
    """The cases whose path is closest to ``case_id``, what they did differently and how long they took."""
    from . import event_log_similar_cases as similar
    _require_dashboard(request)
    return _respond(similar.similar_cases, log_id, case_id, case_type, limit=limit, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/co-occurrence")
def event_log_cooccurrence(request: Request, log_id: str, limit: int = Query(default=12, ge=2, le=25),
                           min_support: int = Query(default=5, ge=1, le=10 ** 6),
                           case_type: str | None = Query(default=None, max_length=200),
                           cohort: dict = Depends(_case_filter)):
    """Which of the most common activities happen in the same cases, regardless of order."""
    from . import event_log_cooccurrence as co
    _require_dashboard(request)
    return _respond(co.cooccurrence, log_id, case_type, limit=limit, min_support=min_support, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/quality/timestamp-ties")
def event_log_quality_timestamp_ties(request: Request, log_id: str,
                                     case_type: str | None = Query(default=None, max_length=200),
                                     cohort: dict = Depends(_case_filter)):
    """Show which activities have ambiguous ordering within selected cases."""
    from . import event_log_quality_ties
    _require_dashboard(request)
    return _respond(event_log_quality_ties.timestamp_ties, log_id, case_type, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/activity-profile")
def event_log_activity_profile(request: Request, log_id: str,
                               profile_activity: str = Query(min_length=1, max_length=500),
                               case_type: str | None = Query(default=None, max_length=200),
                               cohort: dict = Depends(_case_filter)):
    """One activity from every side. ``profile_activity`` names it: ``activity`` is the case filter."""
    from . import event_log_activity_profile as profile
    _require_dashboard(request)
    return _respond(profile.activity_profile, log_id, profile_activity, case_type, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/breakdown/dimensions")
def event_log_breakdown_dimensions(request: Request, log_id: str,
                                   case_type: str | None = Query(default=None, max_length=200)):
    """The dimensions a breakdown table can use in this log."""
    from . import event_log_breakdown as breakdown
    _require_dashboard(request)
    return _respond(breakdown.dimensions, log_id, case_type)


@router.get("/v1/event-logs/{log_id}/breakdown")
def event_log_breakdown(request: Request, log_id: str, rows: str = Query(min_length=1, max_length=220),
                        columns: str | None = Query(default=None, max_length=220),
                        with_activity: str | None = Query(default=None, min_length=1, max_length=500),
                        case_type: str | None = Query(default=None, max_length=200),
                        cohort: dict = Depends(_case_filter)):
    """Case KPIs by one or two dimensions, as a pivot table."""
    from . import event_log_breakdown as breakdown
    _require_dashboard(request)
    return _respond(breakdown.breakdown, log_id, rows, columns, case_type, with_activity=with_activity, cohort=cohort)


@router.post("/v1/event-logs/{log_id}/breakdown/cases")
async def event_log_breakdown_cases(request: Request, log_id: str, rows: str = Query(min_length=1, max_length=220),
                                    columns: str | None = Query(default=None, max_length=220),
                                    with_activity: str | None = Query(default=None, min_length=1, max_length=500),
                                    case_type: str | None = Query(default=None, max_length=200),
                                    cohort: dict = Depends(_case_filter)):
    from . import event_log_breakdown as breakdown
    _require_dashboard(request)
    selection = await _json_body(request, "breakdown case settings", strict=True, limit=8192)
    return await run_in_threadpool(_respond, breakdown.cell_cases, log_id, rows, columns, case_type,
                                   selection=selection, with_activity=with_activity, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/breakdown.csv")
def event_log_breakdown_csv(request: Request, log_id: str, rows: str = Query(min_length=1, max_length=220),
                            columns: str | None = Query(default=None, max_length=220),
                            with_activity: str | None = Query(default=None, min_length=1, max_length=500),
                            case_type: str | None = Query(default=None, max_length=200),
                            cohort: dict = Depends(_case_filter)):
    """The breakdown table as spreadsheet-safe CSV, one line per cell plus totals. Not stored."""
    from . import event_log_breakdown as breakdown
    _require_dashboard(request)
    try:
        text = breakdown.breakdown_csv(log_id, rows, columns, case_type, with_activity=with_activity, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(text, media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-breakdown.csv"'})


@router.get("/v1/event-logs/{log_id}/variants")
def event_log_variants(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                       limit: int = Query(default=20, ge=1, le=200), cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(workspace.variants, log_id, case_type, limit, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/variants.csv")
def event_log_variants_csv(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                           cohort: dict[str, Any] = Depends(_case_filter)):
    """Every variant as spreadsheet-safe CSV: path, cases, share, running share, durations. Not stored."""
    from . import event_log_variant_export as export
    _require_dashboard(request)
    try:
        text, meta = export.variants_csv(log_id, case_type, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(text, media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-variants.csv"',
        "X-OWG-Variants": f"variants={meta['variants']}; written={meta['written']}; truncated={str(meta['truncated']).lower()}"})


@router.get("/v1/event-logs/{log_id}/activities.csv")
def event_log_activities_csv(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                         cohort: dict[str, Any] = Depends(_case_filter)):
    """Every activity of the selection with frequency, repeats, start/end shares and waits, as CSV. Not stored."""
    from . import event_log_statistics_export as export
    _require_dashboard(request)
    try:
        text, meta = export.activities_csv(log_id, case_type, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(text, media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-activities.csv"',
        "X-OWG-Rows": f"rows={meta['rows']}; written={meta['written']}; truncated={str(meta['truncated']).lower()}"})


@router.get("/v1/event-logs/{log_id}/resources.csv")
def event_log_resources_csv(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                         cohort: dict[str, Any] = Depends(_case_filter)):
    """Every resource of the selection with events, cases, activities and waits, as CSV. Not stored."""
    from . import event_log_statistics_export as export
    _require_dashboard(request)
    try:
        text, meta = export.resources_csv(log_id, case_type, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(text, media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-resources.csv"',
        "X-OWG-Rows": f"rows={meta['rows']}; written={meta['written']}; truncated={str(meta['truncated']).lower()}"})


@router.get("/v1/event-logs/{log_id}/variants/preview")
def event_log_variant_preview(request: Request, log_id: str,
        case_type: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=20, ge=1, le=200), cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_variant_preview as variant_preview
    _require_dashboard(request)
    return _respond(variant_preview.preview, log_id, case_type, limit, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/duration-distribution")
def event_log_duration_distribution(request: Request, log_id: str,
                                    case_type: str | None = Query(default=None, max_length=200),
                                    cutoffs_seconds: str = Query(default="3600,14400,86400,259200", max_length=110),
                                    cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    parts = cutoffs_seconds.split(",")
    if not 1 <= len(parts) <= 10 or any(not part.isascii() or not part.isdecimal() or len(part) > 10 for part in parts):
        raise HTTPException(422, "duration thresholds must be comma-separated whole seconds")
    return _respond(duration_distribution.distribution, log_id, [int(part) for part in parts], case_type, cohort)


@router.get("/v1/event-logs/{log_id}/variant-coverage")
def event_log_variant_coverage(request: Request, log_id: str,
                               case_type: str | None = Query(default=None, max_length=200),
                               limit: int = Query(default=20, ge=1, le=200),
                               offset: int = Query(default=0, ge=0, le=variant_coverage.MAX_OFFSET),
                               cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(variant_coverage.coverage, log_id, case_type, limit, cohort=cohort, offset=offset)


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


@router.get("/v1/event-logs/{log_id}/description")
def event_log_description(request: Request, log_id: str):
    """The owner's description of this log ("" when none)."""
    from . import event_log_description as log_description
    _require_dashboard(request)
    return _respond(log_description.description, log_id)


@router.post("/v1/event-logs/{log_id}/description")
async def event_log_set_description(request: Request, log_id: str):
    """Body: {"description": "..."}; an empty text removes it."""
    from . import event_log_description as log_description
    _require_dashboard(request)
    spec = await _json_body(request, "description", strict=True, limit=16384)
    if not isinstance(spec, dict) or set(spec) != {"description"}:
        raise HTTPException(422, 'send {"description": "..."}')
    return _respond(log_description.set_description, log_id, spec["description"])


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


@router.get("/v1/event-logs/{log_id}/saved-reports")
def event_log_saved_reports(request: Request, log_id: str):
    """Copies of the HTML report saved on this computer, newest first."""
    from . import event_log_saved_reports as saved_reports
    _require_dashboard(request)
    return _respond(saved_reports.listing, log_id)


@router.post("/v1/event-logs/{log_id}/saved-reports")
async def event_log_save_report(request: Request, log_id: str):
    """Save a scoped dashboard copy. Older clients can still post without a body."""
    from . import event_log_saved_reports as saved_reports
    _require_dashboard(request)
    if not request.headers.get("content-type"):
        return await run_in_threadpool(_respond, saved_reports.save, log_id)
    spec = await _json_body(request, "saved report scope", strict=True, limit=32768)
    if not isinstance(spec, dict) or set(spec) - {"case_type", "cohort"}:
        raise HTTPException(422, "saved report scope accepts case_type and cohort")
    case_type = spec.get("case_type")
    if case_type is not None and (not isinstance(case_type, str) or not 0 < len(case_type) <= 200):
        raise HTTPException(422, "choose a valid case notion")
    return await run_in_threadpool(_respond, saved_reports.save, log_id, case_type, spec.get("cohort"))

@router.get("/v1/event-logs/{log_id}/saved-report-changes")
def event_log_saved_report_changes(request: Request, log_id: str,
                                   older: str | None = Query(default=None, max_length=80),
                                   newer: str | None = Query(default=None, max_length=80)):
    """What changed between two saved report copies (by default the two newest)."""
    from . import event_log_saved_reports as saved_reports
    _require_dashboard(request)
    return _respond(saved_reports.changes, log_id, older, newer)


@router.get("/v1/event-logs/{log_id}/saved-reports/{name}")
def event_log_saved_report_file(request: Request, log_id: str, name: str):
    from . import event_log_saved_reports as saved_reports
    _require_dashboard(request)
    try:
        target = saved_reports.path(log_id, name)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(404, str(exc)) from None
    return FileResponse(target, media_type="text/html", filename=f"{log_id}-{name}", headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"})


@router.get("/v1/event-logs/{log_id}/report.html")
def event_log_report(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                     sla_seconds: int | None = Query(default=None, ge=1, le=10 ** 9),
                     variants: int = Query(default=15, ge=1, le=50),
                     cohort: dict[str, Any] = Depends(_case_filter)):
    """A self-contained, script-free HTML report (overview, KPIs, variants, activities) to save or share."""
    from fastapi.responses import HTMLResponse

    from . import event_log_report as report
    _require_dashboard(request)
    try:
        html = report.report_html(log_id, case_type, cohort, sla_seconds=sla_seconds, variant_limit=variants)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return HTMLResponse(html, headers={
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{log_id}-report.html"',
        # The report has no scripts or external resources; forbid them if it is opened from the response.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    })


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


@router.get("/v1/event-logs/{log_id}/waiting-time/cases")
def event_log_waiting_hotspot_cases(request: Request, log_id: str,
        source: str = Query(min_length=1, max_length=200), destination: str = Query(min_length=1, max_length=200),
        case_type: str | None = Query(default=None, max_length=200),
        mode: str = Query(default="auto", pattern="^(auto|lifecycle|end_attribute)$"),
        lifecycle_attribute: str = Query(default="lifecycle:transition", min_length=1, max_length=120),
        end_attribute: str = Query(default="", max_length=120),
        after: str | None = Query(default=None, min_length=1, max_length=200),
        limit: int = Query(default=50, ge=1, le=100), cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(waiting.waiting_hotspot_cases, log_id, case_type, source=source, destination=destination,
                     after=after, limit=limit, mode=mode, lifecycle_attribute=lifecycle_attribute,
                     end_attribute=end_attribute, cohort=cohort)


@router.post('/v1/event-logs/{log_id}/waiting-time/cases')
async def event_log_calendar_hotspot_cases(request: Request, log_id: str,
        case_type: str | None = Query(default=None, max_length=200), cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    from pydantic import ValidationError
    from .event_log_calendar_metrics import CalendarHotspotRequest
    body = await _json_body(request, 'scheduled waiting case page', strict=True)
    try:
        spec = CalendarHotspotRequest.model_validate(body)
    except ValidationError:
        raise HTTPException(422, 'Invalid scheduled waiting case settings') from None
    return await run_in_threadpool(_respond, waiting.waiting_hotspot_cases, log_id, case_type,
                                   cohort=cohort, **spec.model_dump())


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


@router.get("/v1/event-logs/{log_id}/performance-spectrum")
def event_log_performance_spectrum(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                                   segment: list[str] = Query(default=[], max_length=12),
                                   top: int = Query(default=6, ge=1, le=12), bins: int = Query(default=30, ge=2, le=100),
                                   cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_performance_spectrum as spectrum
    _require_dashboard(request)

    def run():
        return spectrum.spectrum(log_id, case_type, segments=spectrum.parse_segments(segment), top=top, bins=bins,
                                 cohort=cohort)
    return _respond(run)


@router.post("/v1/event-logs/{log_id}/performance-spectrum/cases")
async def event_log_performance_spectrum_cases(request: Request, log_id: str,
                                                case_type: str | None = Query(default=None, max_length=200),
                                                bins: int = Query(default=30, ge=2, le=100),
                                                cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_performance_spectrum as spectrum
    _require_dashboard(request)
    selection = await _json_body(request, "performance-spectrum case settings", strict=True, limit=8192)
    return await run_in_threadpool(_respond, spectrum.segment_cases, log_id, case_type, selection=selection,
                                   bins=bins, cohort=cohort)


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


@router.post("/v1/event-logs/{log_id}/drift/cases")
async def event_log_drift_window_cases(request: Request, log_id: str,
        case_type: str | None = Query(default=None, max_length=200),
        window: str = Query(default="month", pattern="^(week|month|cases)$"),
        window_cases: int = Query(default=500, ge=20, le=100_000),
        cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    spec = await _json_body(request, "drift case page", strict=True, limit=16_384)
    if not isinstance(spec, dict) or "window_index" not in spec or set(spec) - {
        "window_index", "source", "destination", "after", "expected_window", "expected_sample_sha256", "limit",
    }:
        raise HTTPException(422, "invalid drift case page settings")
    return await run_in_threadpool(_respond, drift.window_cases, log_id, case_type, window=window,
                                   window_size=window_cases, cohort=cohort, **spec)


@router.get("/v1/event-logs/{log_id}/roles")
def event_log_roles(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                    threshold: float = Query(default=0.65, gt=0, lt=1),
                    names: str = Query(default="pseudonymous", max_length=20),
                    cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_roles as roles
    _require_dashboard(request)
    return _respond(roles.roles, log_id, case_type, threshold=threshold, names=names, cohort=cohort)


@router.get("/v1/event-logs/{log_id}/organization")
def event_log_organization(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                           names: str = Query(default="pseudonymous", max_length=20),
                           automation_resource: list[str] | None = Query(default=None, max_length=50),
                           limit: int = Query(default=100, ge=1, le=organization.MAX_EDGES),
                           cohort: dict[str, Any] = Depends(_case_filter)):
    _require_dashboard(request)
    return _respond(organization.organization, log_id, case_type, names=names, cohort=cohort,
                    automation_resources=automation_resource, limit=limit)


@router.post("/v1/event-logs/{log_id}/organization/cases")
async def event_log_organization_cases(request: Request, log_id: str,
                                       case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    spec = await _json_body(request, "relationship case query", strict=True)
    return await run_in_threadpool(_respond, organization.relationship_cases, log_id, spec, case_type)


async def _json_body(request: Request, what: str, *, strict: bool = False, limit: int = 65536) -> Any:
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, f"{what} must be JSON")
    body = await request.body()
    if len(body) > limit:
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


@router.post("/v1/event-logs/{log_id}/compliance/violations")
async def event_log_compliance_violations(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "compliance violation page")
    return await run_in_threadpool(_respond, compliance.violations, log_id, spec)


@router.post("/v1/event-logs/{log_id}/compliance/discover")
async def event_log_rule_discovery(request: Request, log_id: str):
    """Candidate Declare-style rules most cases already follow; nothing is saved or enforced."""
    from . import event_log_rule_discovery as discovery
    _require_dashboard(request)
    spec = await _json_body(request, "rule discovery settings")
    try:
        result = await run_in_threadpool(discovery.discover, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/object-rule-sets")
def event_log_object_rule_sets(request: Request, log_id: str):
    from . import event_log_object_rule_sets as rule_sets
    _require_dashboard(request)
    return _respond(rule_sets.listing, log_id)


@router.post("/v1/event-logs/{log_id}/object-rule-sets")
async def event_log_save_object_rule_set(request: Request, log_id: str):
    from . import event_log_object_rule_sets as rule_sets
    _require_dashboard(request)
    spec = await _json_body(request, "saved object rule set", strict=True)
    return await run_in_threadpool(_respond, rule_sets.save, log_id, spec)


@router.get("/v1/event-logs/{log_id}/object-rule-sets/{set_id}")
def event_log_load_object_rule_set(request: Request, log_id: str, set_id: str):
    from . import event_log_object_rule_sets as rule_sets
    _require_dashboard(request)
    return _respond(rule_sets.get, log_id, set_id)


@router.put("/v1/event-logs/{log_id}/object-rule-sets/{set_id}")
async def event_log_update_object_rule_set(request: Request, log_id: str, set_id: str):
    from . import event_log_object_rule_sets as rule_sets
    _require_dashboard(request)
    spec = await _json_body(request, "saved object rule set", strict=True)
    return await run_in_threadpool(_respond, rule_sets.update, log_id, set_id, spec)


@router.delete("/v1/event-logs/{log_id}/object-rule-sets/{set_id}")
def event_log_delete_object_rule_set(request: Request, log_id: str, set_id: str,
                                     expected_revision: int = Query(ge=1)):
    from . import event_log_object_rule_sets as rule_sets
    _require_dashboard(request)
    return _respond(rule_sets.delete, log_id, set_id, expected_revision)


@router.post("/v1/event-logs/{log_id}/object-constraints")
async def event_log_object_constraints(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object rules", strict=True)
    try:
        result = await run_in_threadpool(object_constraints.check, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/object-constraints/compare")
async def event_log_compare_object_constraints(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object rule comparison", strict=True)
    return await run_in_threadpool(_respond, object_constraints.compare, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-constraints/violations")
async def event_log_object_constraint_violations(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object rule evidence", strict=True)
    return await run_in_threadpool(_respond, object_constraints.violations, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-process-model/discovery")
async def event_log_object_process_model_discovery(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object process model discovery", strict=True)
    try:
        result = await run_in_threadpool(object_process_model.discover, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/object-process-model/preview")
async def event_log_object_process_model_preview(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object process model", strict=True, limit=object_process_model.MAX_REQUEST_BYTES)
    return await run_in_threadpool(_respond, object_process_model.preview, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-process-model/conformance")
async def event_log_object_process_model_conformance(request: Request, log_id: str):
    _require_dashboard(request)
    # Up to eight bounded type models (40 nodes, 100 transitions and 40 rules each).
    spec = await _json_body(request, "object process model", strict=True, limit=object_process_model.MAX_REQUEST_BYTES)
    try:
        result = await run_in_threadpool(object_process_model.check, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/object-process-model/conformance/arc-evidence")
async def event_log_object_process_arc_evidence(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object arc evidence", strict=True, limit=object_process_model.MAX_REQUEST_BYTES + 4096)
    return await run_in_threadpool(_respond, object_process_model.arc_evidence, log_id, spec)


@router.post("/v1/event-logs/{log_id}/impact")
async def event_log_impact(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "impact analysis", strict=True)
    try:
        result = await run_in_threadpool(impact.analyze, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/impact/cases")
async def event_log_impact_cases(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "impact case selection", strict=True)
    return await run_in_threadpool(_respond, impact.cases, log_id, spec)


_PACK_ID = r"^[a-z][a-z0-9_]{0,39}$"


@router.post("/v1/event-logs/{log_id}/domain-pack-labels")
async def event_log_domain_pack_labels(request: Request, log_id: str):
    _require_dashboard(request)
    body = await _json_body(request, "domain pack label search", strict=True, limit=4096)
    if not isinstance(body, dict) or set(body) - {"kind", "search", "after"} or not {"kind", "search"} <= set(body):
        raise HTTPException(422, "label search needs kind and search")
    return await run_in_threadpool(_respond, domain_packs.find_mapping_labels, log_id, body["kind"], body["search"], body.get("after", ""))


@router.get("/v1/event-log-domain-packs")
def event_log_domain_pack_catalog(request: Request):
    _require_dashboard(request)
    return _respond(domain_packs.catalog)


@router.get("/v1/event-log-domain-packs/{pack_id}")
def event_log_domain_pack_definitions(request: Request, pack_id: str = PathParam(pattern=_PACK_ID),
                                      version: str | None = Query(default=None, pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")):
    _require_dashboard(request)
    return _respond(domain_packs.definitions, pack_id, version)


@router.get("/v1/event-logs/{log_id}/domain-packs")
def event_log_domain_packs_installed(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(domain_packs.installed, log_id)


@router.get("/v1/event-logs/{log_id}/domain-packs/{pack_id}/configurations")
def event_log_domain_pack_configurations(request: Request, log_id: str, pack_id: str = PathParam(pattern=_PACK_ID)):
    _require_dashboard(request)
    return _respond(domain_packs.configuration_history, log_id, pack_id)


@router.post("/v1/event-logs/{log_id}/domain-packs/{pack_id}")
async def event_log_domain_pack_install(request: Request, log_id: str, pack_id: str = PathParam(pattern=_PACK_ID)):
    _require_dashboard(request)
    spec = await _json_body(request, "domain pack install", strict=True)
    try:
        result = await run_in_threadpool(domain_packs.install, log_id, pack_id, spec)
    except domain_packs.DomainPackConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.delete("/v1/event-logs/{log_id}/domain-packs/{pack_id}")
def event_log_domain_pack_uninstall(request: Request, log_id: str, pack_id: str = PathParam(pattern=_PACK_ID),
        expected_installation: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$", max_length=64)):
    _require_dashboard(request)
    return _respond(domain_packs.uninstall, log_id, pack_id, expected_installation=expected_installation)


@router.get("/v1/event-logs/{log_id}/domain-packs/{pack_id}/report")
def event_log_domain_pack_report(request: Request, log_id: str, pack_id: str = PathParam(pattern=_PACK_ID)):
    _require_dashboard(request)
    return _respond(domain_packs.report, log_id, pack_id)


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


@router.post("/v1/event-logs/{log_id}/rework")
async def event_log_rework(request: Request, log_id: str):
    """Repeated activities, loop patterns and how cases with rework compare."""
    from . import event_log_rework as rework_analysis
    _require_dashboard(request)
    spec = await _json_body(request, "rework settings", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(rework_analysis.rework, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def _live_prediction_settings(request: Request, what: str, *, strict: bool = False,
                                    limit: int = 65536) -> tuple[Any, str | None]:
    spec = await _json_body(request, what, strict=strict, limit=limit)
    if not isinstance(spec, dict) or "case_id" not in spec:
        return spec, None
    case_id = spec.pop("case_id")
    if not isinstance(case_id, str) or not 1 <= len(case_id) <= 200:
        raise HTTPException(422, "case_id must be a non-empty case key of at most 200 characters")
    return spec, case_id


@router.post("/v1/event-logs/{log_id}/predictions")
async def event_log_predictions(request: Request, log_id: str, rank_by: predictions.PredictionOrder = "automatic"):
    _require_dashboard(request)
    spec, case_id = await _live_prediction_settings(request, "prediction settings")
    try:
        result = await run_in_threadpool(predictions.predict, log_id, spec, case_id=case_id, rank_by=rank_by)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/prediction-snapshots")
async def event_log_take_prediction_snapshot(request: Request, log_id: str):
    from . import event_log_prediction_snapshots as prediction_snapshots
    _require_dashboard(request)
    spec = await _json_body(request, "prediction settings")
    try:
        result = await run_in_threadpool(prediction_snapshots.take, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/next-activity")
async def event_log_next_activity(request: Request, log_id: str):
    """What usually happens next in open cases, learned from completed cases and backtested."""
    from . import event_log_next_activity as next_activity
    _require_dashboard(request)
    spec, case_id = await _live_prediction_settings(request, "next-activity settings", strict=True, limit=32768)
    try:
        result = await run_in_threadpool(next_activity.next_activity, log_id, spec, case_id=case_id, include_evidence=True)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/next-activity/training-evidence")
async def event_log_next_activity_training_evidence(request: Request, log_id: str):
    from . import event_log_next_activity as next_activity
    _require_dashboard(request)
    spec = await _json_body(request, "next-activity training evidence", strict=True, limit=32768)
    return await run_in_threadpool(_respond, next_activity.training_evidence, log_id, spec)


@router.get("/v1/event-logs/{log_id}/prediction-snapshots")
async def event_log_list_prediction_snapshots(request: Request, log_id: str):
    from . import event_log_prediction_snapshots as prediction_snapshots
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(prediction_snapshots.listing, log_id)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(404, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/prediction-snapshots/{snapshot_id}/evaluation")
async def event_log_evaluate_prediction_snapshot(request: Request, log_id: str, snapshot_id: str):
    from . import event_log_prediction_snapshots as prediction_snapshots
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(prediction_snapshots.evaluate, log_id, snapshot_id)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(404 if "unknown" in str(exc) else 422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/prediction-snapshots/{snapshot_id}/delete")
async def event_log_delete_prediction_snapshot(request: Request, log_id: str, snapshot_id: str):
    from . import event_log_prediction_snapshots as prediction_snapshots
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(prediction_snapshots.delete, log_id, snapshot_id)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(404, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/predictions/monitoring")
async def event_log_prediction_monitoring(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "monitoring settings", strict=True)
    try:
        result = await run_in_threadpool(prediction_monitoring.monitor, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/activity-glossary")
def event_log_activity_glossary(request: Request, log_id: str):
    """The owner's notes per activity (description, owner team, system, automation) with coverage."""
    from . import event_log_activity_glossary as glossary
    _require_dashboard(request)
    return _respond(glossary.glossary, log_id)


@router.post("/v1/event-logs/{log_id}/activity-glossary")
async def event_log_set_activity_note(request: Request, log_id: str):
    """Body: {"activity", "description"?, "owner_team"?, "system"?, "automation"?}. All fields empty removes the entry."""
    from . import event_log_activity_glossary as glossary
    _require_dashboard(request)
    spec = await _json_body(request, "glossary entry", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(glossary.set_note, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/activity-glossary/delete")
async def event_log_delete_activity_note(request: Request, log_id: str):
    from . import event_log_activity_glossary as glossary
    _require_dashboard(request)
    spec = await _json_body(request, "glossary entry", strict=True, limit=2048)
    if not isinstance(spec, dict) or set(spec) != {"activity"}:
        raise HTTPException(422, 'send {"activity": "..."}')
    try:
        result = await run_in_threadpool(glossary.delete_note, log_id, spec["activity"])
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/activity-glossary/import")
async def event_log_import_activity_glossary(request: Request, log_id: str):
    """Body: the glossary as CSV text (activity, description, owner_team, system, automation)."""
    from . import event_log_activity_glossary as glossary
    _require_dashboard(request)
    body = await request.body()
    if len(body) > glossary.MAX_IMPORT_BYTES:
        raise HTTPException(413, "the glossary CSV is larger than 1 MB")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "the glossary CSV must be UTF-8") from None
    try:
        result = await run_in_threadpool(glossary.import_csv, log_id, text)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/activity-glossary.csv")
def event_log_activity_glossary_csv(request: Request, log_id: str):
    """The glossary as spreadsheet-safe CSV, one line per activity. Not stored."""
    from . import event_log_activity_glossary as glossary
    _require_dashboard(request)
    try:
        text = glossary.glossary_csv(log_id)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(text, media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-activity-glossary.csv"'})


@router.post("/v1/event-logs/{log_id}/batching")
async def event_log_batching(request: Request, log_id: str):
    """Batch processing inferred from timestamps and resources, with simulation proposals (R14)."""
    from . import event_log_batching as batch_detection
    _require_dashboard(request)
    spec = await _json_body(request, "batch settings", strict=True, limit=8192)
    try:
        result = await run_in_threadpool(batch_detection.batching, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/batching/batches")
async def event_log_batches(request: Request, log_id: str):
    from . import event_log_batching as batch_detection
    _require_dashboard(request)
    spec = await _json_body(request, "batch list settings", strict=True, limit=16384)
    return await run_in_threadpool(_respond, batch_detection.batches, log_id, spec)


@router.post("/v1/event-logs/{log_id}/batching/batches/cases")
async def event_log_batch_cases(request: Request, log_id: str):
    from . import event_log_batching as batch_detection
    _require_dashboard(request)
    spec = await _json_body(request, "batch case settings", strict=True, limit=16384)
    return await run_in_threadpool(_respond, batch_detection.batch_cases, log_id, spec)


@router.post("/v1/event-logs/{log_id}/linked-attribute-analysis")
async def event_log_linked_attribute_analysis(request: Request, log_id: str):
    """Read-only cardinality-aware joined object attribute breakdown (R9)."""
    _require_dashboard(request)
    spec = await _json_body(request, "linked attribute analysis", strict=True, limit=8192)
    try:
        result = await run_in_threadpool(linked_attribute_analysis.analyze, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/work-in-progress")
async def event_log_work_in_progress(request: Request, log_id: str):
    """Arrivals, completions and open cases per day or week, with a Little's law check."""
    from . import event_log_wip as wip
    _require_dashboard(request)
    spec = await _json_body(request, "work-in-progress settings", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(wip.work_in_progress, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/work-in-progress/cases")
async def event_log_work_in_progress_cases(request: Request, log_id: str):
    from . import event_log_wip as wip
    _require_dashboard(request)
    spec = await _json_body(request, "work-in-progress case settings", strict=True, limit=16384)
    return await run_in_threadpool(_respond, wip.bucket_cases, log_id, spec)


@router.post("/v1/event-logs/{log_id}/analysis")
async def event_log_run_analysis(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "analysis")
    try:
        result = await run_in_threadpool(analysis.run, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/analysis-source-bound")
async def event_log_run_analysis_source_bound(request: Request, log_id: str):
    """Return full local analysis and source lineage under one workspace lock."""
    from . import event_log_analysis_review as review
    _require_dashboard(request)
    spec = await _json_body(request, "source-bound analysis")
    response = await run_in_threadpool(_respond, review.source_bound, log_id, spec)
    _require_dashboard(request)
    return response


@router.post("/v1/event-logs/{log_id}/analysis/groups/cases")
async def event_log_analysis_group_cases(request: Request, log_id: str):
    """Return one page of cases in a previously reviewed analysis group."""
    from . import event_log_analysis_review as review
    _require_dashboard(request)
    body = await _json_body(request, "analysis group case review", strict=True, limit=131072)
    response = await run_in_threadpool(_respond, review.group_cases, log_id, body)
    _require_dashboard(request)
    return response


@router.post("/v1/event-logs/{log_id}/case-sharing-preview")
async def event_log_case_sharing_preview(request: Request, log_id: str):
    _require_dashboard(request)
    if request.headers.get('x-owg-case-sharing-preview') != 'true':
        raise HTTPException(403,'Explicit case-level sharing preview confirmation is required')
    from .analysis_case_export import prepare
    parsed = await _json_body(request, 'analysis')
    return await run_in_threadpool(_respond, prepare, log_id, parsed)


@router.post("/v1/event-logs/{log_id}/company-handoff-preview")
async def event_log_company_handoff_preview(request: Request, log_id: str):
    """Explicitly prepare one paired, minimized file for separate human publication."""
    _require_dashboard(request)
    if request.headers.get("x-owg-company-handoff-preview") != "true":
        raise HTTPException(403, "Explicit aggregate and case handoff preview confirmation required")
    spec = await _json_body(request, "company handoff preview")
    from .analysis_handoff_bundle import prepare as prepare_handoff
    try:
        packet = await run_in_threadpool(prepare_handoff, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError:
        raise HTTPException(422, "Paired handoff exceeds format bounds or has inconsistent evidence") from None
    return JSONResponse(packet, headers={"Cache-Control": "no-store"})


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


@router.post("/v1/event-logs/{log_id}/analysis-sharing-freshness")
async def analysis_sharing_freshness(request: Request, log_id: str):
    _require_dashboard(request)
    if request.headers.get('x-owg-analysis-sharing-preview') != 'true':
        raise HTTPException(403, 'Explicit sharing-preview request required')
    body = await _json_body(request, 'analysis snapshot freshness', strict=True)
    try:
        result = await run_in_threadpool(analysis_snapshot_export.check_snapshot_freshness, log_id, body)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError:
        raise HTTPException(422, 'Analysis cannot fit the bounded sharing format') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


def _analysis_grant_digest(body: dict) -> str:
    """Bind a grant to the complete canonical reviewed analysis request."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


@router.post("/v1/event-logs/{log_id}/analysis-sharing-authorize")
async def analysis_sharing_authorize(request: Request, log_id: str):
    """Issue one short-lived grant only after a fresh, explicit review."""
    _require_dashboard(request)
    if request.headers.get("x-owg-analysis-sharing-preview") != "true":
        raise HTTPException(403, "Explicit sharing-preview confirmation required")
    body = await _json_body(request, "source-bound analysis authorization", strict=True)
    from . import local_auth
    session = str(request.headers.get("authorization") or "")[len("OWG-Session "):].strip()
    try:
        await run_in_threadpool(analysis_snapshot_export.export_if_source_current, log_id, body)
        grant = local_auth.issue_analysis_grant(session, log_id, _analysis_grant_digest(body))
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(409, str(exc)) from None
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid bounded analysis authorization") from None
    return JSONResponse(grant, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/analysis-sharing-download")
async def analysis_sharing_download(request: Request, log_id: str):
    """Atomically burn the session-bound grant before recomputing source lineage."""
    _require_dashboard(request)
    if request.headers.get("x-owg-analysis-sharing-preview") != "true":
        raise HTTPException(403, "Explicit sharing-preview confirmation required")
    body = await _json_body(request, "source-bound analysis download", strict=True)
    from . import local_auth
    if not isinstance(body, dict):
        raise HTTPException(422, "Invalid analysis download")
    ticket = body.pop("ticket", None)
    session = str(request.headers.get("authorization") or "")[len("OWG-Session "):].strip()
    try:
        digest = _analysis_grant_digest(body)
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid analysis download") from None
    if not isinstance(ticket, str) or not local_auth.consume_analysis_grant(ticket, session, log_id, digest):
        raise HTTPException(403, "Expired, consumed or mismatched analysis authorization")
    try:
        result = await run_in_threadpool(analysis_snapshot_export.export_if_source_current, log_id, body)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError:
        raise HTTPException(422, "Analysis cannot fit the bounded sharing format") from None
    # Revocation can happen while the expensive source recomputation runs in a
    # thread. Do not release numeric rows to a session revoked in that interval.
    if not local_auth.dashboard_session_valid(session):
        raise HTTPException(403, "Dashboard session revoked during analysis download")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/analyses")
def event_log_saved_analyses(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(analysis.list_saved, log_id)


@router.post("/v1/event-logs/{log_id}/analyses")
async def event_log_save_analysis(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(analysis.save, log_id, await _json_body(request, "saved analysis"))


@router.get("/v1/event-logs/{log_id}/analyses/{analysis_id}/template")
def event_log_export_analysis_template(request: Request, log_id: str, analysis_id: str):
    _require_dashboard(request)
    response = _respond(analysis.export_template, log_id, analysis_id)
    response.headers["Content-Disposition"] = f'attachment; filename="{analysis_id}-analysis.json"'
    return response


@router.post("/v1/event-logs/{log_id}/analyses/template/preview")
async def event_log_preview_analysis_template(request: Request, log_id: str):
    _require_dashboard(request)
    document = await _json_body(request, "saved analysis file", strict=True)
    return await run_in_threadpool(_respond, analysis.use_template, log_id, document, preview=True)


@router.post("/v1/event-logs/{log_id}/analyses/template/import")
async def event_log_import_analysis_template(request: Request, log_id: str):
    _require_dashboard(request)
    document = await _json_body(request, "saved analysis file", strict=True)
    return await run_in_threadpool(_respond, analysis.use_template, log_id, document, preview=False)


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
def event_log_filter_sets(request: Request, log_id: str,
                          case_type: str | None = Query(default=None, max_length=200)):
    _require_dashboard(request)
    return _respond(filter_sets.list_sets, log_id, case_type)


@router.post("/v1/event-logs/{log_id}/filter-sets")
async def event_log_save_filter_set(request: Request, log_id: str):
    _require_dashboard(request)
    return _respond(filter_sets.save, log_id, await _json_body(request, "filter set"))


@router.post("/v1/event-logs/{log_id}/filter-sets/import")
async def event_log_import_filter_set(request: Request, log_id: str):
    from . import event_log_filter_sets as filters
    _require_dashboard(request)
    spec = await _json_body(request, "filter file", strict=True, limit=65536)
    return await run_in_threadpool(_respond, filters.import_set, log_id, spec)


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


@router.post("/v1/event-logs/{log_id}/signals/preview")
async def event_log_preview_signal_rule(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "signal rule", strict=True)
    return await run_in_threadpool(_respond, signals.preview_rule, log_id, spec)


@router.get("/v1/event-logs/{log_id}/signals/pack")
def event_log_export_signal_pack(request: Request, log_id: str):
    _require_dashboard(request)
    response = _respond(signals.export_pack, log_id)
    response.headers["Content-Disposition"] = f'attachment; filename="{log_id}-signals.json"'
    return response


@router.post("/v1/event-logs/{log_id}/signals/pack/preview")
async def event_log_preview_signal_pack(request: Request, log_id: str):
    _require_dashboard(request)
    document = await _json_body(request, "signal pack", strict=True, limit=signals.MAX_PACK_BYTES)
    return await run_in_threadpool(_respond, signals.preview_pack, log_id, document)


@router.post("/v1/event-logs/{log_id}/signals/pack/import")
async def event_log_import_signal_pack(request: Request, log_id: str):
    _require_dashboard(request)
    document = await _json_body(request, "signal pack", strict=True, limit=signals.MAX_PACK_BYTES)
    return await run_in_threadpool(_respond, signals.import_pack, log_id, document)


@router.get("/v1/event-logs/{log_id}/signals/scorecard")
def event_log_signal_scorecard(request: Request, log_id: str, months: int = Query(default=6, ge=1, le=24)):
    """Value-based signals month by month (read-only)."""
    _require_dashboard(request)
    return _respond(signals.scorecard, log_id, months)


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


@router.get("/v1/event-logs/{log_id}/signal-history")
def event_log_signal_history(request: Request, log_id: str,
                             limit: int = Query(default=50, ge=1, le=200),
                             before: str = Query(default="", max_length=256)):
    _require_dashboard(request)
    return _respond(signal_transitions.list_history, log_id, limit=limit, before=before)


@router.post("/v1/event-logs/{log_id}/signal-inbox/acknowledge-batch")
async def event_log_signal_batch_acknowledge(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "batch signal acknowledgement", strict=True)
    return _respond(signal_inbox.acknowledge_batch, log_id, spec)


@router.post("/v1/event-logs/{log_id}/signal-inbox/{rule_id}/snooze")
async def event_log_signal_snooze(request: Request, log_id: str, rule_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "signal snooze", strict=True)
    return _respond(signal_inbox.snooze, log_id, rule_id, spec)


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


@router.post("/v1/event-logs/{log_id}/trace-clusters")
async def event_log_trace_clusters(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "trace clustering")
    return await run_in_threadpool(_respond, trace_clusters.clusters, log_id, spec)


@router.post("/v1/event-logs/{log_id}/trace-clusters/cases")
async def event_log_trace_cluster_cases(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "trace cluster case settings", strict=True)
    return await run_in_threadpool(_respond, trace_clusters.cluster_cases, log_id, spec)


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


@router.get("/v1/event-logs/{log_id}/model-discovery.pnml")
def event_log_model_discovery_pnml(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                                   noise: float = Query(default=0.2, ge=0, lt=1),
                                   activities: float = Query(default=1.0, gt=0, le=1),
                                   cohort: dict[str, Any] = Depends(_case_filter)):
    from . import event_log_pnml as pnml
    _require_dashboard(request)
    try:
        xml, meta = pnml.discovered_pnml(log_id, case_type, noise=noise, activities=activities, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(xml, media_type="application/xml", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-discovered.pnml"',
        "X-OWG-Petri-Net": f"places={meta['places']}; transitions={meta['transitions']}; silent={meta['silent_transitions']}"})


@router.get("/v1/event-logs/{log_id}/variant.pnml")
def event_log_variant_pnml(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                           rank: int = Query(default=1, ge=1, le=50), cohort: dict[str, Any] = Depends(_case_filter)):
    """A sequence Petri net of the rank-th most common path (rank 1: the happy path)."""
    from . import event_log_pnml as pnml
    _require_dashboard(request)
    try:
        xml, meta = pnml.variant_pnml(log_id, case_type, rank=rank, cohort=cohort)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(xml, media_type="application/xml", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{log_id}-path-{rank}.pnml"',
        "X-OWG-Path-Cases": f"cases={meta['cases']}; share={meta['share']}"})


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
                         outcome_tag: str | None = Query(default=None, max_length=60),
                         limit: int = Query(default=30, ge=1, le=100)):
    """``outcome=tagged`` with ``outcome_tag``: which features go with the cases carrying that tag."""
    _require_dashboard(request)
    return _respond(analytics.root_cause, log_id, outcome, case_type, threshold_seconds=threshold_seconds,
                    activity=activity, limit=limit, tag=outcome_tag)


@router.get("/v1/event-logs/{log_id}/simulation/calibration")
def event_log_simulation_calibration(request: Request, log_id: str,
                                     case_type: str | None = Query(default=None, max_length=200),
                                     max_paths: int = Query(default=10, ge=1, le=simulation.MAX_PATHS),
                                     routing: str = Query(default="paths", max_length=20),
                                     routing_context: str | None = Query(default=None, max_length=80)):
    _require_dashboard(request)
    return _respond(simulation.calibrate, log_id, case_type, max_paths, routing=routing, routing_context=routing_context)


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


@router.post("/v1/event-logs/{log_id}/simulation/portfolio")
async def event_log_simulation_portfolio(request: Request, log_id: str,
                                         case_type: str | None = Query(default=None, max_length=200)):
    """Review 2–4 changed process simulations against one calibrated baseline."""
    _require_dashboard(request)
    spec = await _json_body(request, "simulation portfolio", strict=True, limit=65536)
    try:
        result = await run_in_threadpool(simulation_portfolio.compare, log_id, spec, case_type)
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


@router.get("/v1/event-logs/{log_id}/ocel.sqlite")
def export_event_log_ocel_sqlite(request: Request, log_id: str):
    """Export the retained OCEL 2.0 log into a temporary SQLite database, owner-only."""
    import shutil
    from . import event_log_ocel_sqlite as ocel_sqlite
    _require_dashboard(request)
    folder = tempfile.mkdtemp(prefix="owg-ocel-sqlite-", dir=str(DATA_DIR))
    path = Path(folder) / "log.sqlite"
    try:
        ocel_sqlite.write(log_id, path)
    except workspace.EventLogWorkspaceError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(exc)) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return FileResponse(str(path), media_type="application/vnd.sqlite3",
                        filename=f"{log_id}.ocel.sqlite", headers={"Cache-Control": "no-store"},
                        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))


@router.get("/v1/event-logs/{log_id}/events.xlsx")
def export_event_log_xlsx(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200)):
    """The log's events as an Excel workbook (the CSV export's rows, as text cells). Not stored."""
    import shutil
    from . import event_log_xlsx_export as xlsx_export
    _require_dashboard(request)
    folder = tempfile.mkdtemp(prefix="owg-xlsx-export-", dir=str(DATA_DIR))
    path = Path(folder) / "events.xlsx"
    try:
        xlsx_export.write(log_id, path, case_type)
    except workspace.EventLogWorkspaceError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(exc)) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return FileResponse(str(path), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        filename=f"{log_id}.xlsx", headers={"Cache-Control": "no-store"},
                        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))


@router.get("/v1/event-logs/{log_id}/export")
def export_event_log(request: Request, log_id: str, format: str = Query(pattern="^(csv|sourcecsv|csv-review|sourcecsv-review|xes|xes-attributes|ocel|ocelxml)$"),
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


@router.post("/v1/event-logs/{log_id}/cases/explain")
async def event_log_explain_case(request: Request, log_id: str):
    """Where one case's time went, compared with the typical gap of each transition."""
    from . import event_log_case_explain as case_explain
    _require_dashboard(request)
    spec = await _json_body(request, "case explanation settings", strict=True, limit=8192)
    try:
        result = await run_in_threadpool(case_explain.explain, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/cases/slowest")
async def event_log_slowest_cases(request: Request, log_id: str):
    from . import event_log_case_explain as case_explain
    _require_dashboard(request)
    spec = await _json_body(request, "slowest case settings", strict=True, limit=16384)
    try:
        result = await run_in_threadpool(case_explain.slowest, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


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


@router.get("/v1/event-logs/{log_id}/cohort-metric-definitions")
def event_log_list_cohort_metric_definitions(request: Request, log_id: str):
    _require_dashboard(request)
    from . import event_log_saved_cohort_metrics as saved
    return _respond(saved.list_saved, log_id)


@router.post("/v1/event-logs/{log_id}/cohort-metric-definitions")
async def event_log_save_cohort_metric_definition(request: Request, log_id: str):
    _require_dashboard(request)
    from . import event_log_saved_cohort_metrics as saved
    spec = await _json_body(request, "saved cohort metric", strict=True, limit=8192)
    response = await run_in_threadpool(_respond, saved.save, log_id, spec)
    _require_dashboard(request)
    return response


@router.post("/v1/event-logs/{log_id}/cohort-metric-definitions/{metric_id}/delete")
async def event_log_delete_cohort_metric_definition(request: Request, log_id: str, metric_id: str):
    _require_dashboard(request)
    from . import event_log_saved_cohort_metrics as saved
    response = await run_in_threadpool(_respond, saved.delete, log_id, metric_id)
    _require_dashboard(request)
    return response


@router.post("/v1/event-logs/{log_id}/cohort-metric-definitions/{metric_id}/evaluate")
async def event_log_evaluate_saved_cohort_metric(
    request: Request, log_id: str, metric_id: str,
    case_type: str | None = Query(default=None, max_length=200),
):
    """Evaluate a saved, log-scoped metric without accepting a new expression."""
    _require_dashboard(request)
    body = await _json_body(request, "saved cohort metric evaluation", strict=True, limit=8192)
    if not isinstance(body, dict) or set(body) != {"cohort"}:
        raise HTTPException(422, "cohort is required")
    from . import event_log_saved_cohort_metrics as saved
    response = await run_in_threadpool(
        _respond, saved.evaluate, log_id, metric_id, body["cohort"], case_type
    )
    _require_dashboard(request)
    return response


@router.post("/v1/event-logs/{log_id}/cohort-metric")
async def event_log_cohort_metric(
    request: Request, log_id: str,
    case_type: str | None = Query(default=None, max_length=200),
):
    """Compute a bounded numeric metric on a saved, explicitly selected case cohort."""
    _require_dashboard(request)
    spec = await _json_body(request, "cohort metric", strict=True, limit=8192)
    from . import event_log_cohort_metrics
    response = await run_in_threadpool(
        _respond, event_log_cohort_metrics.summarize, log_id, spec, case_type
    )
    # A session can be revoked while DuckDB is computing the aggregate.
    # Check again before releasing even aggregate-only numeric results.
    _require_dashboard(request)
    return response


@router.get("/v1/event-logs/{log_id}/activity-mappings")
async def event_log_activity_mappings(request: Request, log_id: str):
    from . import event_log_activity_mappings as activity_mappings
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(activity_mappings.listing, log_id)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(404, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/hidden-activities")
def event_log_hidden_activities(request: Request, log_id: str):
    """The activities left out of case-centric analyses, and every activity with its event count."""
    from . import event_log_hidden_activities as hidden_activities
    _require_dashboard(request)
    return _respond(hidden_activities.hidden, log_id)


@router.post("/v1/event-logs/{log_id}/hidden-activities")
async def event_log_set_hidden_activities(request: Request, log_id: str):
    """Body ``{"activities": [...]}`` replaces the hidden list; an empty list shows every activity."""
    from . import event_log_hidden_activities as hidden_activities
    _require_dashboard(request)
    spec = await _json_body(request, "hidden activities", strict=True, limit=131072)
    try:
        result = await run_in_threadpool(hidden_activities.set_hidden, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/activity-label-suggestions")
def event_log_activity_label_suggestions(request: Request, log_id: str):
    """Groups of original activity names that are probably the same activity (mapping suggestions)."""
    from . import event_log_label_suggestions as labels
    _require_dashboard(request)
    return _respond(labels.suggestions, log_id)


@router.post("/v1/event-logs/{log_id}/activity-mappings")
async def event_log_create_activity_mapping(request: Request, log_id: str):
    from . import event_log_activity_mappings as activity_mappings
    _require_dashboard(request)
    spec = await _json_body(request, "activity mapping", limit=262144)
    try:
        result = await run_in_threadpool(activity_mappings.create, log_id, spec)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/activity-mappings/applied")
async def event_log_apply_activity_mapping(request: Request, log_id: str):
    """Body ``{"mapping_id": "amap_..."}`` applies one mapping; ``{"mapping_id": null}`` removes it."""
    from . import event_log_activity_mappings as activity_mappings
    _require_dashboard(request)
    body = await _json_body(request, "applied activity mapping")
    if not isinstance(body, dict) or set(body) != {"mapping_id"} or not (body["mapping_id"] is None or isinstance(body["mapping_id"], str)):
        raise HTTPException(422, "send {\"mapping_id\": <id or null>}")
    try:
        result = await run_in_threadpool(activity_mappings.apply_mapping, log_id, body["mapping_id"])
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(404 if "unknown" in str(exc) else 422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/event-logs/{log_id}/activity-mappings/{mapping_id}/delete")
async def event_log_delete_activity_mapping(request: Request, log_id: str, mapping_id: str):
    from . import event_log_activity_mappings as activity_mappings
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(activity_mappings.delete, log_id, mapping_id)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(404, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
