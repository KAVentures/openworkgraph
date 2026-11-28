"""Dedicated local whole-window policy review, with no ledger writes."""
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "policy_window_review.js"


@router.get("/policy-window-review.js")
def script():
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.post("/v1/process-catalog/clarify/effective-window-review")
async def review(request: Request, workflow_id: str = Query(min_length=1,max_length=128),
                 revision: int = Query(ge=1)):
    from .workflow_evidence_routes import _catalog_record, _process_json, _PROCESS_ID
    from .local_source_routes import _require_dashboard
    from shared import process_policy_window as window
    import re
    _require_dashboard(request)
    if not re.fullmatch(_PROCESS_ID,workflow_id):
        raise HTTPException(422,"invalid process identifier")
    try:
        body = window.Review.model_validate(await _process_json(request,maximum=1536*1024))
    except (ValidationError,ValueError):
        raise HTTPException(422,"invalid bounded policy window review") from None
    _require_dashboard(request)
    record = _catalog_record(workflow_id,revision)
    try:
        result = await run_in_threadpool(window.review,record,body)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None
    _require_dashboard(request)
    return JSONResponse(result,headers={"Cache-Control":"no-store"})
