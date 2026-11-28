"""Local read-only planning of a pinned implementation proposal batch."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import ai_implementation_batch as batch
from shared import ai_implementation_planning as planning
from shared import ai_implementation_review as contract
from . import ai_implementation_batch_routes as batch_routes

router = APIRouter()
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'ai_implementation_planning.js'


def documents(payload, *, comparing=False):
    fields = batch_routes.FIELDS | {'batch_review_sha256', 'plan_json' if comparing else 'currency'}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError('Exact planning inputs required')
    sources, text = payload['sources'], payload['proposals_json']
    if (not isinstance(sources, list) or not 1 <= len(sources) <= contract.MAX_BUNDLES
            or not all(isinstance(s, str) for s in sources) or not isinstance(text, str)
            or not 0 < len(text.encode('utf-8')) <= batch.MAX_BATCH_BYTES):
        raise ValueError('Invalid source or batch size')
    args = ([batch_routes.decode(s) for s in sources], payload['packet_sha256'],
            batch_routes.decode(text), payload['batch_review_sha256'])
    if comparing:
        text = payload['plan_json']
        if not isinstance(text, str) or not 0 < len(text.encode('utf-8')) <= planning.MAX_PLAN_BYTES:
            raise ValueError('Invalid planning input size')
        plan = batch_routes.decode(text)
        result = planning.compare(*args, plan)
        return {'format': 'openworkgraph.ai-implementation-planning-result.v1',
                'planning_review_sha256': result['planning_review_sha256'],
                'plan_json': contract._json(plan).decode('utf-8'),
                'report_json': contract._json(result).decode('utf-8'),
                'portfolio_written': False, 'execution_authorized': False}
    result = planning.worklist(*args, payload['currency'])
    return {'format': 'openworkgraph.ai-implementation-planning-template.v1',
            'worklist_json': contract._json(result).decode('utf-8'),
            'portfolio_written': False, 'execution_authorized': False}


@router.get('/ai-implementation-planning.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


async def process(request, *, comparing):
    payload = await batch_routes.body(request)
    try:
        result = await run_in_threadpool(documents, payload, comparing=comparing)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Planning operation refused; inspect exact batch pins, assumptions and dependencies') from None
    batch_routes.require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.post('/v1/ai-opportunity-handoff/planning-worklist')
async def worklist(request: Request):
    return await process(request, comparing=False)


@router.post('/v1/ai-opportunity-handoff/planning-compare')
async def compare(request: Request):
    return await process(request, comparing=True)
