"""Local dashboard-only join of existing native and retained source evidence reviews."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import joint_shadow_source_review as joint
from . import project_source_checks as checks
from . import automation_pipeline as pipeline
from .local_auth import dashboard_session_valid

router = APIRouter()
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'joint_shadow_source_review.js'
MAX_REQUEST_BYTES = 24 * 1024 * 1024
MAX_TRACE_BYTES = 8 * 1024 * 1024
MAX_SMALL_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024


def require_dashboard(request):
    auth = request.headers.get('authorization', '')
    if not auth.lower().startswith('owg-session ') or not dashboard_session_valid(auth[len('owg-session '):].strip()):
        raise HTTPException(403, 'Local dashboard session required')


def decode(text):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate key')
            result[k] = v
        return result

    def constant(_):
        raise ValueError('nonfinite JSON')

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def original(value, limit):
    if not isinstance(value, str) or not 0 < len(value.encode('utf-8')) <= limit:
        raise ValueError('Original document exceeds byte budget')
    return decode(value)


@router.get('/joint-shadow-source-review.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


@router.post('/v1/automation-pipeline/{idea_id}/projects/{project_id}/joint-shadow-source-review')
async def review(request: Request, idea_id: str, project_id: str):
    require_dashboard(request)
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON request required')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, 'Joint review exceeds request budget')
        raw.extend(chunk)
    require_dashboard(request)
    try:
        data = decode(raw.decode('utf-8'))
        if not isinstance(data, dict) or set(data) != {
                'plan_json', 'plan_sha256', 'trace_json', 'trace_sha256', 'assignments_json', 'source_cohort'}:
            raise ValueError('Original documents required')
        if len(json.dumps(data['source_cohort'], ensure_ascii=False, allow_nan=False).encode()) > MAX_SOURCE_BYTES:
            raise ValueError('Source cohort exceeds byte budget')
        payload = {'plan': original(data['plan_json'], MAX_SMALL_BYTES), 'plan_sha256': data['plan_sha256'],
                   'trace': original(data['trace_json'], MAX_TRACE_BYTES), 'trace_sha256': data['trace_sha256'],
                   'assignments': original(data['assignments_json'], MAX_SMALL_BYTES), 'source_cohort': data['source_cohort']}
        result = await run_in_threadpool(joint.review, idea_id, project_id, payload,
                                         options=checks.options, reconcile=checks.review_export)
    except pipeline.PipelineError:
        raise HTTPException(422, 'Project or retained source evidence unavailable or changed') from None
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Joint review refused; check the frozen roster, trace, assignments and original source documents') from None
    require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})
