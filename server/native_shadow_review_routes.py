"""Local dashboard bridge for the existing native shadow trace reviewer."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import native_shadow_trace_review as shadow
from .local_auth import dashboard_session_valid

router = APIRouter()
MAX_TRACE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 18 * 1024 * 1024
ROW_LIMIT = 25000
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'native_shadow_review.js'


def require_dashboard(request):
    value = request.headers.get('authorization', '')
    if not value.lower().startswith('owg-session ') or not dashboard_session_valid(value[len('owg-session '):].strip()):
        raise HTTPException(403, 'Local dashboard session required')


def window(since, until):
    try:
        start, end = [datetime.fromisoformat(x.replace('Z', '+00:00')) for x in (since, until)]
        if start.utcoffset() is None or end.utcoffset() is None or not timedelta(0) < end-start <= timedelta(days=7):
            raise ValueError()
        return tuple(x.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') for x in (start, end))
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise HTTPException(422, 'Select an explicit offset-aware window of at most seven days') from None


def read_trace(request, since, until, include_disconnected):
    from .agent_execution_trace_routes import get_agent_execution_traces
    return get_agent_execution_traces(request, since=since, until=until, limit=100,
                                      evidence_limit=ROW_LIMIT, max_events_per_execution=500,
                                      hide_disconnected=not include_disconnected)


def decode(text):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate key')
            result[k] = v
        return result
    value = json.loads(text, object_pairs_hook=pairs)
    shadow.canonical(value)  # Reject nonfinite JSON values.
    return value


@router.get('/native-shadow-review.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


@router.get('/v1/native-shadow-review/snapshot')
def snapshot(request: Request, since: str, until: str, include_disconnected: bool = False):
    require_dashboard(request)
    since, until = window(since, until)
    trace = read_trace(request, since, until, include_disconnected)
    require_dashboard(request)
    try:
        if trace['executions']:
            shadow.inventory(trace)
        raw = shadow.canonical(trace)
        if len(raw) > MAX_TRACE_BYTES:
            raise HTTPException(413, 'Snapshot exceeds 8 MiB; select a narrower window')
        result = {'format': 'openworkgraph.native-shadow-snapshot.v1', 'trace_json': raw.decode('utf-8'),
                  'trace_sha256': shadow.digest(trace), 'since': since, 'until': until,
                  'include_disconnected': include_disconnected, 'returned': trace['returned'],
                  'source_row_limit_may_have_been_reached': trace['evidence_rows_considered'] >= ROW_LIMIT,
                  'execution_authorized': False}
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise HTTPException(422, 'Invalid or unsupported trace snapshot; narrow the window') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.post('/v1/native-shadow-review/review')
async def review(request: Request):
    require_dashboard(request)
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON request required')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, 'Review request exceeds byte budget')
        raw.extend(chunk)
    require_dashboard(request)
    try:
        payload = decode(raw.decode('utf-8'))
        if (not isinstance(payload, dict) or set(payload) != {'trace_json','trace_sha256','declaration'} or
                not isinstance(payload['trace_json'], str) or len(payload['trace_json'].encode()) > MAX_TRACE_BYTES):
            raise ValueError('invalid review request')
        trace = decode(payload['trace_json'])
        result = await run_in_threadpool(shadow.review, trace, payload['trace_sha256'], payload['declaration'])
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise HTTPException(422, 'Trace review refused; check the exact snapshot and case declarations') from None
    require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})
