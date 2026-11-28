"""Read-only single-source proposal compatibility for the local dashboard."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import ai_implementation_compat as compat
from shared import ai_implementation_review as contract
from . import ai_implementation_batch_routes as batch_routes

router = APIRouter()
FIELDS = {'source_json', 'packet_sha256', 'evidence_sha256', 'sectioned_review_sha256', 'sectioned_proposal_json'}
MAX_PROPOSAL = 1024 * 1024
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'ai_implementation_compat.js'


def documents(payload, *, reviewing=False):
    fields = FIELDS | {'handoff_sha256', 'integrated_proposal_json'} if reviewing else FIELDS
    compat.require(isinstance(payload, dict) and set(payload) == fields, 'Exact compatibility inputs required')
    def decode(key, limit):
        raw = payload[key]
        compat.require(isinstance(raw, str) and 0 < len(raw.encode('utf-8')) <= limit, 'Invalid document size')
        return batch_routes.decode(raw)
    source = decode('source_json', contract.MAX_BYTES)
    old = decode('sectioned_proposal_json', MAX_PROPOSAL)
    args = (source, payload['evidence_sha256'], old, payload['sectioned_review_sha256'])
    handoff = compat.prepare(*args)
    compat.require(handoff['packet_sha256'] == payload['packet_sha256'], 'Prepared packet changed')
    if reviewing:
        proposal = decode('integrated_proposal_json', MAX_PROPOSAL)
        result = compat.review(*args, payload['handoff_sha256'], proposal)
        return {'format':'openworkgraph.ai-implementation-compatibility-review-result.v1',
                'report_json':contract._json(result).decode('utf-8'),
                'proposal_json':contract._json(proposal).decode('utf-8'),
                'compatibility_review_sha256':result['compatibility_review_sha256'],
                'execution_authorized':False, 'portfolio_written':False}
    return {'format':'openworkgraph.ai-implementation-compatibility-handoff-result.v1',
            'handoff_json':contract._json(handoff).decode('utf-8'),
            'handoff_sha256':handoff['handoff_sha256'], 'execution_authorized':False, 'portfolio_written':False}


@router.get('/ai-implementation-compat.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


async def process(request, reviewing):
    payload = await batch_routes.body(request)
    try:
        result = await run_in_threadpool(documents, payload, reviewing=reviewing)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Compatibility refused; inspect the original source, proposals and exact pins') from None
    batch_routes.require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})


@router.post('/v1/ai-opportunity-handoff/compat-prepare')
async def prepare(request: Request):
    return await process(request, False)


@router.post('/v1/ai-opportunity-handoff/compat-review')
async def review(request: Request):
    return await process(request, True)
