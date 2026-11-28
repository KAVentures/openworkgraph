"""Read-only dashboard batch review; individual idea creation stays separate."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import ai_implementation_batch as batch
from shared import ai_implementation_review as contract
from .local_auth import dashboard_session_valid

router = APIRouter()
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'ai_implementation_batch.js'
MAX_REQUEST = 8 * 1024 * 1024
FIELDS = {'sources', 'packet_sha256', 'proposals_json'}


def require_dashboard(request):
    auth = request.headers.get('authorization', '')
    if not auth.lower().startswith('owg-session ') or not dashboard_session_valid(auth[len('owg-session '):].strip()):
        raise HTTPException(403, 'Local dashboard session required')


def decode(text):
    if not isinstance(text, str):
        raise ValueError('JSON document required')
    value = json.loads(text, object_pairs_hook=contract._unique_object)
    contract._json(value)
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
        return decode(raw.decode('utf-8'))
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Invalid JSON request') from None


def documents(payload, *, selecting=False):
    expected = FIELDS | {'batch_review_sha256', 'item_index'} if selecting else FIELDS
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError('Exact batch inputs required')
    sources, text = payload['sources'], payload['proposals_json']
    if (not isinstance(sources, list) or not 1 <= len(sources) <= contract.MAX_BUNDLES
            or not all(isinstance(s, str) for s in sources) or not isinstance(text, str)
            or not 0 < len(text.encode('utf-8')) <= batch.MAX_BATCH_BYTES):
        raise ValueError('Invalid source or batch size')
    sources, proposals = [decode(s) for s in sources], decode(text)
    if selecting:
        chosen = batch.select(sources, payload['packet_sha256'], proposals,
                              payload['batch_review_sha256'], payload['item_index'])
        return {'format': 'openworkgraph.ai-implementation-batch-selection.v1',
                'packet_sha256': payload['packet_sha256'], 'batch_review_sha256': payload['batch_review_sha256'],
                'item_index': payload['item_index'], 'proposal_sha256': contract._digest(chosen),
                'proposal_json': contract._json(chosen).decode('utf-8'), 'execution_authorized': False,
                'portfolio_written': False, 'human_review_required': True}
    report = batch.review_batch(sources, payload['packet_sha256'], proposals)
    return {'format': 'openworkgraph.ai-implementation-batch-preview.v1',
            'report_json': contract._json(report).decode('utf-8'),
            'batch_review_sha256': report['batch_review_sha256'], 'execution_authorized': False,
            'portfolio_written': False}


@router.get('/ai-implementation-batch.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


async def process(request, *, selecting):
    payload = await body(request)
    try:
        result = await run_in_threadpool(documents, payload, selecting=selecting)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Batch operation refused; inspect exact source, batch and review pins') from None
    require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.post('/v1/ai-opportunity-handoff/batch-review')
async def review(request: Request):
    return await process(request, selecting=False)


@router.post('/v1/ai-opportunity-handoff/batch-select')
async def select(request: Request):
    return await process(request, selecting=True)
