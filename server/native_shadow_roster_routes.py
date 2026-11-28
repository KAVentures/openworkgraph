"""Local dashboard access to the existing frozen native shadow roster."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import native_shadow_roster as roster
from .local_auth import dashboard_session_valid

router = APIRouter()
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'native_shadow_roster.js'
MAX_REQUEST = 18 * 1024 * 1024
MAX_TRACE = 8 * 1024 * 1024
MAX_SMALL = 256 * 1024


def require_dashboard(request):
    auth = request.headers.get('authorization', '')
    if not auth.lower().startswith('owg-session ') or not dashboard_session_valid(auth[len('owg-session '):].strip()):
        raise HTTPException(403, 'Local dashboard session required')


def decode(text, limit):
    if not isinstance(text, str) or not 0 < len(text.encode('utf-8')) <= limit:
        raise ValueError('Document exceeds byte budget')
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate key')
            result[k] = v
        return result
    value = json.loads(text, object_pairs_hook=pairs)
    roster.trace_review.canonical(value)
    return value


async def body(request):
    require_dashboard(request)
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON request required')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST:
            raise HTTPException(413, 'Request exceeds byte budget')
        raw.extend(chunk)
    require_dashboard(request)
    try:
        return decode(raw.decode('utf-8'), MAX_REQUEST)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Invalid JSON request') from None


def freeze_documents(payload):
    if not isinstance(payload, dict) or set(payload) != {'manifest_json','package_sha256','cases_json','confirm_freeze'}:
        raise ValueError('Exact roster inputs required')
    plan = roster.freeze(decode(payload['manifest_json'], MAX_SMALL), payload['package_sha256'],
                         decode(payload['cases_json'], MAX_SMALL), confirmed=payload['confirm_freeze'])
    worklist = roster.worklist(plan, plan['plan_sha256'])
    encode = lambda value: roster.trace_review.canonical(value).decode('utf-8')
    return {'format':'openworkgraph.native-shadow-roster-preview.v1', 'plan_json':encode(plan),
            'worklist_json':encode(worklist), 'plan_sha256':plan['plan_sha256'],
            'package_sha256':plan['package_sha256'], 'case_count':len(worklist),
            'execution_authorized':False, 'prospective_enrollment_verified':False}


def review_documents(payload):
    if not isinstance(payload, dict) or set(payload) != {'plan_json','plan_sha256','trace_json','trace_sha256','assignments_json'}:
        raise ValueError('Exact reconciliation inputs required')
    result = roster.review(decode(payload['plan_json'], MAX_SMALL), payload['plan_sha256'],
                           decode(payload['trace_json'], MAX_TRACE), payload['trace_sha256'],
                           decode(payload['assignments_json'], MAX_SMALL))
    return {'report_json':roster.trace_review.canonical(result).decode('utf-8'),
            'review_sha256':result['review_sha256'], 'execution_authorized':False}


@router.get('/native-shadow-roster.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


@router.post('/v1/native-shadow-roster/freeze')
async def freeze(request: Request):
    payload = await body(request)
    try:
        result = await run_in_threadpool(freeze_documents, payload)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Roster refused; check the original manifest, independent pin, cases and confirmation') from None
    require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})


@router.post('/v1/native-shadow-roster/review')
async def review(request: Request):
    payload = await body(request)
    try:
        result = await run_in_threadpool(review_documents, payload)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Review refused; check original plan and trace pins and complete assignments') from None
    require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})
