"""Local routes for the automation opportunity pipeline (dashboard session only)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import automation_pipeline as pipeline
from . import opportunity_projects
from . import project_report_links
from . import project_source_checks
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "automation_pipeline.js"


def _require_dashboard(request: Request) -> None:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required for the automation pipeline")


async def _json(request: Request) -> Any:
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, "request must be JSON")
    body = await request.body()
    if len(body) > 32768:
        raise HTTPException(413, "request is too large")
    try:
        return json.loads(body)
    except ValueError:
        raise HTTPException(422, "request is not valid JSON") from None


def _respond(operation: Callable[..., Any], *args: Any) -> JSONResponse:
    try:
        return JSONResponse(operation(*args), headers={"Cache-Control": "no-store"})
    except pipeline.PipelineError as exc:
        status = 409 if "changed" in str(exc) else 404 if str(exc) == "unknown idea" else 422
        raise HTTPException(status, str(exc)) from None


@router.get("/automation-pipeline.js")
def automation_pipeline_script() -> FileResponse:
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.get("/v1/automation-pipeline")
def list_pipeline(request: Request, state: str | None = Query(default=None, max_length=20)):
    _require_dashboard(request)
    return _respond(pipeline.pipeline, state)


@router.get("/v1/automation-pipeline/export.csv")
def export_pipeline(request: Request, state: str | None = Query(default=None, max_length=20)):
    _require_dashboard(request)
    try:
        body = pipeline.export_csv(state)
    except pipeline.PipelineError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(body, media_type="text/csv", headers={
        "Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="automation-pipeline.csv"'})


@router.post("/v1/automation-pipeline")
async def create_idea(request: Request):
    _require_dashboard(request)
    return _respond(pipeline.create, await _json(request))


@router.post("/v1/automation-pipeline/from-process")
async def create_idea_from_process(request: Request):
    _require_dashboard(request)
    return _respond(pipeline.create_from_process, await _json(request))


@router.post("/v1/automation-pipeline/{idea_id}/agent-project")
async def export_idea_project(request: Request, idea_id: str):
    _require_dashboard(request)
    try:
        payload = await _json(request)
        archive, record = await run_in_threadpool(opportunity_projects.export_project, idea_id, payload)
    except pipeline.PipelineError as exc:
        status = 409 if "changed" in str(exc) else 404 if str(exc) == "unknown idea" else 422
        raise HTTPException(status, str(exc)) from None
    return Response(archive, media_type="application/zip", headers={
        "Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="owg-opportunity-project.zip"',
        "X-OWG-Project-ID": record['project_id'], "X-OWG-Archive-SHA256": record['archive_sha256']})


@router.get("/v1/automation-pipeline/{idea_id}/projects/{project_id}/source-check-options")
def source_check_options(request: Request,idea_id: str,project_id: str):
    _require_dashboard(request)
    return _respond(project_source_checks.options,idea_id,project_id)


@router.get("/v1/automation-pipeline/{idea_id}/projects/{project_id}/source-check-exports")
def source_check_exports(request: Request,idea_id: str,project_id: str):
    _require_dashboard(request)
    return _respond(project_source_checks.exports,idea_id,project_id)


@router.post("/v1/automation-pipeline/{idea_id}/projects/{project_id}/source-check-export")
async def export_source_check(request: Request,idea_id: str,project_id: str):
    _require_dashboard(request)
    try:
        content,metadata=await run_in_threadpool(project_source_checks.export_check,idea_id,project_id,await _json(request))
    except project_source_checks.ConsentRequired as exc:
        raise HTTPException(403,str(exc)) from None
    except pipeline.PipelineError as exc:
        status=409 if "changed" in str(exc) else 404 if str(exc)=="unknown idea" else 422
        raise HTTPException(status,str(exc)) from None
    return Response(content,media_type="application/json",headers={
        "Cache-Control":"no-store","Content-Disposition":'attachment; filename="owg-project-source-check.json"',
        "X-OWG-Source-Export-ID":metadata['export_id'],"X-OWG-Document-SHA256":metadata['document_sha256']})


@router.get("/v1/automation-pipeline/{idea_id}/project-reports")
def list_project_reports(request: Request, idea_id: str):
    _require_dashboard(request)
    return _respond(project_report_links.links, idea_id)


@router.post("/v1/automation-pipeline/{idea_id}/project-reports")
async def link_project_report(request: Request, idea_id: str):
    _require_dashboard(request)
    return await run_in_threadpool(_respond, project_report_links.link, idea_id, await _json(request))


@router.post("/v1/automation-pipeline/{idea_id}/project-reports/unlink")
async def unlink_project_report(request: Request, idea_id: str):
    _require_dashboard(request)
    return await run_in_threadpool(_respond, project_report_links.unlink, idea_id, await _json(request))


@router.get("/v1/automation-pipeline/{idea_id}/projects")
def list_idea_projects(request: Request, idea_id: str):
    _require_dashboard(request)
    return _respond(opportunity_projects.projects, idea_id)


@router.get("/v1/automation-pipeline/{idea_id}/process-status")
def process_status(request: Request, idea_id: str):
    _require_dashboard(request)
    return _respond(pipeline.process_status, idea_id)


@router.get("/v1/automation-pipeline/{idea_id}")
def get_idea(request: Request, idea_id: str):
    _require_dashboard(request)
    return _respond(pipeline.get, idea_id)


@router.post("/v1/automation-pipeline/{idea_id}/assess")
async def assess_idea(request: Request, idea_id: str):
    _require_dashboard(request)
    return _respond(pipeline.assess, idea_id, await _json(request))


@router.post("/v1/automation-pipeline/{idea_id}/transition")
async def move_idea(request: Request, idea_id: str):
    _require_dashboard(request)
    return _respond(pipeline.transition, idea_id, await _json(request))


@router.post("/v1/automation-pipeline/{idea_id}/delete")
def delete_idea(request: Request, idea_id: str):
    _require_dashboard(request)
    return _respond(pipeline.delete, idea_id)
