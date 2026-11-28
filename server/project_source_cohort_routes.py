"""Local dashboard-only retained source-check cohort review."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import project_source_cohort as cohort
from . import project_source_checks as checks
from . import automation_pipeline as pipeline
from .local_auth import dashboard_session_valid

router = APIRouter()
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'project_source_cohort.js'
MAX_REQUEST_BYTES = 4 * 1024 * 1024


def _require_dashboard(request):
    auth = request.headers.get('authorization', '')
    if not auth.lower().startswith('owg-session ') or not dashboard_session_valid(auth[len('owg-session '):].strip()):
        raise HTTPException(403, 'Local dashboard session required')


@router.get('/project-source-cohort.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


@router.post('/v1/automation-pipeline/{idea_id}/projects/{project_id}/source-check-cohort-review')
async def review(request: Request, idea_id: str, project_id: str):
    _require_dashboard(request)
    if request.headers.get('content-type','').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON request required')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, 'Cohort review exceeds the 4 MiB request budget')
        raw.extend(chunk)

    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate key')
            result[k] = v
        return result

    def constant(_):
        raise ValueError('nonfinite JSON')

    _require_dashboard(request)
    try:
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        result = await run_in_threadpool(cohort.review, idea_id, project_id, payload,
                                         options=checks.options, reconcile=checks.review_export)
    except pipeline.PipelineError:
        raise HTTPException(422, 'Project or retained source-check review unavailable or changed') from None
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Cohort review refused; check the declared cases, exact original documents and project revision') from None
    _require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})

from .joint_shadow_source_routes import router as _joint_review_router
router.include_router(_joint_review_router)
