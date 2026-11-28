"""Local dashboard-only bridge from external AI proposals to unassessed ideas."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import ai_opportunity_handoff as handoff
from . import automation_pipeline as pipeline
from .local_auth import dashboard_session_valid

router = APIRouter()
MAX_BYTES = 8 * 1024 * 1024
SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "ai_opportunity_handoff.js"


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
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise HTTPException(422, "Invalid JSON input") from None


@router.get("/ai-opportunity-handoff.js")
def script():
    return FileResponse(SCRIPT, media_type="application/javascript")


@router.post("/v1/ai-opportunity-handoff/preview")
async def preview(request: Request):
    payload = await _body(request)
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(handoff.prepare, payload)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, "Proposal review refused; check the original evidence, proposal and human-entered idea") from None
    _require_dashboard(request)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/ai-opportunity-handoff/commit")
async def commit(request: Request):
    payload = await _body(request)
    _require_dashboard(request)
    try:
        idea, lineage = await run_in_threadpool(handoff.confirm, payload)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, "Creation refused; review the exact preview and explicitly confirm again") from None
    _require_dashboard(request)
    try:
        result = await run_in_threadpool(pipeline.create_from_ai_review, idea, lineage)
    except pipeline.PipelineError as exc:
        status = 409 if "already recorded" in str(exc) else 422
        raise HTTPException(status, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
