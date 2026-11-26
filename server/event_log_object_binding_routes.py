"""Local-only UI and API for supplied OCEL event-binding continuity."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import event_log_object_binding_continuity as bindings
from .event_log_routes import _json_body, _require_dashboard, _respond

router = APIRouter()
_DASH = Path(__file__).resolve().parents[1] / "dashboard"
_PAGE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@router.get("/object-binding-continuity")
def object_binding_continuity_page():
    return FileResponse(_DASH / "object_binding_continuity.html", media_type="text/html",
                        headers={**_PAGE_HEADERS, "Content-Security-Policy": _CSP})


@router.get("/object-binding-continuity.js")
def object_binding_continuity_script():
    return FileResponse(_DASH / "object_binding_continuity.js",
                        media_type="application/javascript", headers=_PAGE_HEADERS)


@router.post("/v1/event-logs/{log_id}/object-bindings/continuity")
async def object_binding_continuity(request: Request, log_id: str):
    _require_dashboard(request)
    spec = await _json_body(request, "object binding continuity settings",
                            strict=True, limit=16_384)
    return await run_in_threadpool(_respond, bindings.analyze, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-state-check")
async def object_state_check(request: Request, log_id: str):
    from . import event_log_object_state_check as state
    _require_dashboard(request)
    spec = await _json_body(request, "object state check settings", strict=True, limit=16384)
    _require_dashboard(request)
    return await run_in_threadpool(_respond, state.analyze, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-state-check/witnesses")
async def object_state_check_witnesses(request: Request, log_id: str):
    from . import event_log_object_state_check as state
    _require_dashboard(request)
    spec = await _json_body(request, "object state witness settings", strict=True, limit=16384)
    _require_dashboard(request)
    return await run_in_threadpool(_respond, state.witnesses, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-state-check/distribution")
async def object_state_check_distribution(request: Request, log_id: str):
    from . import event_log_object_state_check as state
    _require_dashboard(request)
    spec = await _json_body(request, "object state distribution settings", strict=True, limit=16384)
    _require_dashboard(request)
    return await run_in_threadpool(_respond, state.distribution, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-state-check/distribution/export")
async def export_object_state_distribution(request: Request, log_id: str):
    from . import event_log_object_state_check as state
    from . import event_log_workspace as workspace
    _require_dashboard(request)
    spec = await _json_body(request, "object state distribution export settings", strict=True, limit=16384)

    def prepare():
        workspace.DATA_DIR.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', suffix='.review.csv',
                                             dir=str(workspace.DATA_DIR), delete=False)
        try:
            with handle:
                result = state.export_distribution(log_id, spec, handle)
        except BaseException:
            os.unlink(handle.name)
            raise
        return handle.name, result

    try:
        filename, result = await run_in_threadpool(prepare)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        _require_dashboard(request)
    except BaseException:
        os.unlink(filename)
        raise
    return FileResponse(filename, media_type='text/csv', filename=f'{log_id}-object-state-distribution.review.csv',
                        headers={'Cache-Control':'no-store', 'X-OWG-Rows':str(result['rows'])},
                        background=BackgroundTask(os.unlink, filename))


@router.post("/v1/event-logs/{log_id}/object-state-check/timeline")
async def object_state_check_timeline(request: Request, log_id: str):
    from . import event_log_object_state_check as state
    _require_dashboard(request)
    spec = await _json_body(request, "object state timeline settings", strict=True, limit=16384)
    _require_dashboard(request)
    return await run_in_threadpool(_respond, state.timeline, log_id, spec)


@router.post("/v1/event-logs/{log_id}/object-state-check/labels")
async def object_state_check_labels(request: Request, log_id: str):
    from . import event_log_object_state_labels as labels
    _require_dashboard(request)
    spec = await _json_body(request, "object state label lookup", strict=True, limit=16384)
    _require_dashboard(request)
    return await run_in_threadpool(_respond, labels.search, log_id, spec)


@router.get("/v1/event-logs/{log_id}/object-state-check/saved")
def list_saved_object_state_checks(request: Request, log_id: str):
    from . import event_log_object_state_saved as saved
    _require_dashboard(request)
    return _respond(saved.listing, log_id)


@router.post("/v1/event-logs/{log_id}/object-state-check/template/preview")
async def preview_object_state_check_template(request: Request, log_id: str):
    from . import event_log_object_state_saved as saved
    _require_dashboard(request)
    document = await _json_body(request, "object state definition file", strict=True, limit=65536)
    _require_dashboard(request)
    return await run_in_threadpool(_respond, saved.preview_template, log_id, document)


@router.post("/v1/event-logs/{log_id}/object-state-check/saved")
async def save_object_state_check(request: Request, log_id: str):
    from . import event_log_object_state_saved as saved
    _require_dashboard(request)
    spec = await _json_body(request, "saved object state check", strict=True, limit=16384)
    _require_dashboard(request)
    return await run_in_threadpool(_respond, saved.save, log_id, spec)


@router.get("/v1/event-logs/{log_id}/object-state-check/saved/{check_id}")
def get_saved_object_state_check(request: Request, log_id: str, check_id: str = PathParam(min_length=1, max_length=80)):
    from . import event_log_object_state_saved as saved
    _require_dashboard(request)
    return _respond(saved.get, log_id, check_id)


@router.put("/v1/event-logs/{log_id}/object-state-check/saved/{check_id}")
async def update_saved_object_state_check(request: Request, log_id: str, check_id: str = PathParam(min_length=1, max_length=80)):
    from . import event_log_object_state_saved as saved
    _require_dashboard(request)
    spec = await _json_body(request, "saved object state check", strict=True, limit=16384)
    _require_dashboard(request)
    return await run_in_threadpool(_respond, saved.update, log_id, check_id, spec)


@router.delete("/v1/event-logs/{log_id}/object-state-check/saved/{check_id}")
def delete_saved_object_state_check(request: Request, log_id: str,
        check_id: str = PathParam(min_length=1, max_length=80),
        expected_revision: int = Query(ge=1, le=2_147_483_647)):
    from . import event_log_object_state_saved as saved
    _require_dashboard(request)
    return _respond(saved.delete, log_id, check_id, expected_revision)


@router.post("/v1/event-logs/{log_id}/object-state-check/timeline/export")
async def export_object_state_timeline(request: Request, log_id: str):
    from . import event_log_object_state_check as state
    from . import event_log_workspace as workspace
    _require_dashboard(request)
    spec = await _json_body(request, "object state timeline export settings", strict=True, limit=16384)

    def prepare():
        workspace.DATA_DIR.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', suffix='.review.csv',
                                             dir=str(workspace.DATA_DIR), delete=False)
        try:
            with handle:
                result = state.export_timeline(log_id, spec, handle)
        except BaseException:
            os.unlink(handle.name)
            raise
        return handle.name, result

    try:
        filename, result = await run_in_threadpool(prepare)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        _require_dashboard(request)
    except BaseException:
        os.unlink(filename)
        raise
    return FileResponse(filename, media_type='text/csv', filename=f'{log_id}-object-state-timeline.review.csv',
                        headers={'Cache-Control':'no-store', 'X-OWG-Rows':str(result['rows'])},
                        background=BackgroundTask(os.unlink, filename))


@router.post("/v1/event-logs/{log_id}/object-state-check/export")
async def export_object_state_check(request: Request, log_id: str):
    from . import event_log_object_state_check as state
    from . import event_log_workspace as workspace
    _require_dashboard(request)
    spec = await _json_body(request, "object state export settings", strict=True, limit=16384)

    def prepare():
        workspace.DATA_DIR.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', suffix='.review.csv',
                                             dir=str(workspace.DATA_DIR), delete=False)
        try:
            with handle:
                result = state.export_worklist(log_id, spec, handle)
        except BaseException:
            os.unlink(handle.name)
            raise
        return handle.name, result

    try:
        filename, result = await run_in_threadpool(prepare)
    except workspace.EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        _require_dashboard(request)
    except BaseException:
        os.unlink(filename)
        raise
    return FileResponse(filename, media_type='text/csv', filename=f'{log_id}-object-state-{spec["kind"]}.review.csv',
                        headers={'Cache-Control':'no-store', 'X-OWG-Rows':str(result['rows'])},
                        background=BackgroundTask(os.unlink, filename))


__all__ = ["router"]
