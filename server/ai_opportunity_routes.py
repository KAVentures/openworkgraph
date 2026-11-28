"""Local dashboard-only bridge from external AI proposals to unassessed ideas."""
import http.client
import json
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import ai_opportunity_handoff as handoff
from shared import ai_implementation_packet as packet_handoff
from shared import ai_implementation_local_handoff as local_handoff
from shared import ai_implementation_trial_handoff as trial_handoff
from . import automation_pipeline as pipeline
from .local_auth import dashboard_session_valid

router = APIRouter()
from .ai_implementation_batch_routes import router as _batch_router
router.include_router(_batch_router)
from .ai_implementation_planning_routes import router as _planning_router
router.include_router(_planning_router)
from .ai_implementation_portfolio_routes import router as _portfolio_link_router
router.include_router(_portfolio_link_router)
from .ai_implementation_portfolio_handoff_routes import router as _portfolio_handoff_router
router.include_router(_portfolio_handoff_router)
from .ai_implementation_forecast_routes import router as _forecast_router
router.include_router(_forecast_router)
from .ai_implementation_improvement_routes import router as _improvement_router
router.include_router(_improvement_router)
from .ai_implementation_compat_routes import router as _compat_router
router.include_router(_compat_router)
MAX_BYTES = 8 * 1024 * 1024
_LOCAL_MODEL_SLOT = Lock()
SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "ai_opportunity_handoff.js"
TRIAL_SCRIPT = SCRIPT.with_name("ai_context_trial.js")
EVIDENCE_PICKER_SCRIPT = SCRIPT.with_name("ai_implementation_evidence_picker.js")


def _require_dashboard(request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required")


async def _body(request):
    _require_dashboard(request)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Select local JSON files")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Selected files exceed the 8 MiB request budget")
        raw.extend(chunk)

    try:
        return _decode(raw.decode("utf-8", errors="strict"))
    except UnicodeError:
        raise HTTPException(422, "Invalid JSON input") from None


def _decode(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("nonfinite JSON")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise HTTPException(422, "Invalid JSON input") from None


def _draft(value):
    # Preserve the selected original JSON files until strict server-side parsing.
    if not isinstance(value, dict) or set(value) != {"sources", "proposal", "title", "description"}:
        raise HTTPException(422, "Select original evidence and proposal JSON files")
    sources = value["sources"]
    if (not isinstance(sources, list) or not 1 <= len(sources) <= 8 or
            not all(isinstance(s, str) for s in sources) or not isinstance(value["proposal"], str)):
        raise HTTPException(422, "Select one to eight original evidence JSON files and one proposal")
    return {**value, "sources": [_decode(s) for s in sources], "proposal": _decode(value["proposal"])}


@router.get("/ai-opportunity-handoff.js")
def script():
    return FileResponse(SCRIPT, media_type="application/javascript")


@router.get("/ai-context-trial.js")
def trial_script():
    return FileResponse(TRIAL_SCRIPT, media_type="application/javascript")


@router.get("/ai-implementation-evidence-picker.js")
def evidence_picker_script():
    return FileResponse(EVIDENCE_PICKER_SCRIPT, media_type="application/javascript")


@router.get("/ai-implementation-portfolio-handoff.js")
def portfolio_handoff_script():
    return FileResponse(SCRIPT.with_name("ai_implementation_portfolio_handoff.js"), media_type="application/javascript")


@router.post("/v1/ai-opportunity-handoff/prepare-packet")
async def prepare_packet(request: Request):
    payload = await _body(request)
    _require_dashboard(request)
    if not isinstance(payload, dict) or set(payload) != {"sources", "max_bytes"}:
        raise HTTPException(422, "Select original evidence JSON files and a byte budget")
    sources = payload["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8 or not all(isinstance(s, str) for s in sources):
        raise HTTPException(422, "Select one to eight original evidence JSON files")
    try:
        result = await run_in_threadpool(packet_handoff.preview, [_decode(s) for s in sources], payload["max_bytes"])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, "Packet preparation refused; check the original evidence or increase the byte budget. No evidence is silently dropped.") from None
    _require_dashboard(request)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def _generate_local(payload, request):
    _require_dashboard(request)
    if not _LOCAL_MODEL_SLOT.acquire(blocking=False):
        raise HTTPException(409, "A local model request is already in progress")
    try:
        # Keep the slot until the blocking network request has actually ended.
        _require_dashboard(request)
        return local_handoff.generate(payload)
    finally:
        _LOCAL_MODEL_SLOT.release()


@router.post("/v1/ai-opportunity-handoff/generate-local")
async def generate_local(request: Request):
    payload = await _body(request)
    _require_dashboard(request)
    required = {"sources", "expected_packet_sha256", "max_bytes", "model", "port", "max_output_tokens", "json_mode", "confirm_local_inference"}
    if not isinstance(payload, dict) or set(payload) not in (required, required | {"context_representation"}):
        raise HTTPException(422, "Exact local model request required")
    sources = payload["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8 or not all(isinstance(s, str) for s in sources):
        raise HTTPException(422, "Select one to eight original evidence JSON files")
    if payload["confirm_local_inference"] is not True:
        raise HTTPException(422, "Explicit local model confirmation required")
    try:
        result = await run_in_threadpool(_generate_local, {**payload, "sources": [_decode(s) for s in sources]}, request)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, OSError, http.client.HTTPException):
        raise HTTPException(422, "Local model proposal refused; check the current evidence, model service, budget or response. No retry or cloud fallback occurred.") from None
    _require_dashboard(request)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def _run_context_trial(payload, request):
    _require_dashboard(request)
    if not _LOCAL_MODEL_SLOT.acquire(blocking=False):
        raise HTTPException(409, "A local model request is already in progress")
    try:
        # Both calls have been explicitly authorized as one trial. Keep the
        # shared slot until both attempts finish, including on client disconnect.
        _require_dashboard(request)
        return trial_handoff.run(payload)
    finally:
        _LOCAL_MODEL_SLOT.release()


@router.post("/v1/ai-opportunity-handoff/context-trial")
async def context_trial(request: Request):
    payload = await _body(request)
    _require_dashboard(request)
    if not isinstance(payload, dict) or set(payload) != trial_handoff.FIELDS:
        raise HTTPException(422, "Exact paired context experiment request required")
    sources = payload["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8 or not all(isinstance(s, str) for s in sources):
        raise HTTPException(422, "Select one to eight original evidence JSON files")
    if payload["confirm_two_requests"] is not True:
        raise HTTPException(422, "Explicit confirmation of both model requests required")
    try:
        result = await run_in_threadpool(_run_context_trial, {**payload, "sources": [_decode(s) for s in sources]}, request)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, OSError, http.client.HTTPException):
        raise HTTPException(422, "Paired context experiment refused; inspect evidence, settings and service. No automatic retry or fallback.") from None
    _require_dashboard(request)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/ai-opportunity-handoff/preview")
async def preview(request: Request):
    payload = await _body(request)
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(handoff.prepare, _draft(payload))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, "Proposal review refused; check the original evidence, proposal and human-entered idea") from None
    _require_dashboard(request)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/ai-opportunity-handoff/commit")
async def commit(request: Request):
    payload = await _body(request)
    _require_dashboard(request)
    if not isinstance(payload, dict) or set(payload) != {"draft", "expected_preview_sha256", "confirm_create_idea"}:
        raise HTTPException(422, "Exact preview and confirmation required")
    try:
        idea, lineage = await run_in_threadpool(handoff.confirm, {**payload, "draft": _draft(payload["draft"])})
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, "Creation refused; review the exact preview and explicitly confirm again") from None
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(pipeline.create_from_ai_review, idea, lineage)
    except pipeline.PipelineError as exc:
        status = 409 if "already recorded" in str(exc) else 422
        raise HTTPException(status, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
