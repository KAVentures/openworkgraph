"""Local dashboard-only stress review and separate client module."""
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "investment_stress.js"


@router.get("/investment-stress.js")
def script():
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.post("/v1/automation-pipeline/{idea_id}/investment-case/stress")
async def review(request: Request, idea_id: str):
    from .automation_pipeline_routes import _require_dashboard, _benefit
    from .event_log_routes import _json_body
    from . import automation_investment_stress as stress
    _require_dashboard(request)
    payload = await _json_body(request, "investment stress review", strict=True, limit=65536)
    _require_dashboard(request)
    result = await run_in_threadpool(_benefit, stress.evaluate, idea_id, payload)
    _require_dashboard(request)
    return result
