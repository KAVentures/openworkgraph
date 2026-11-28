"""Read the current local portfolio against one pinned planning review."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import ai_implementation_batch as batch
from shared import ai_implementation_planning as planning
from shared import ai_implementation_portfolio_link as link
from shared import ai_implementation_review as contract
from . import ai_implementation_batch_routes as batch_routes

router = APIRouter()
FIELDS = batch_routes.FIELDS | {'batch_review_sha256', 'plan_json', 'planning_review_sha256'}


def current_ideas():
    from . import automation_pipeline
    return automation_pipeline.pipeline()['ideas']


def documents(payload):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError('Exact planning inputs required')
    sources, proposals, plan = (payload[k] for k in ('sources', 'proposals_json', 'plan_json'))
    if (not isinstance(sources, list) or not 1 <= len(sources) <= contract.MAX_BUNDLES
            or not all(isinstance(s, str) for s in sources)
            or not isinstance(proposals, str) or not 0 < len(proposals.encode('utf-8')) <= batch.MAX_BATCH_BYTES
            or not isinstance(plan, str) or not 0 < len(plan.encode('utf-8')) <= planning.MAX_PLAN_BYTES):
        raise ValueError('Invalid source, proposal or planning size')
    args = ([batch_routes.decode(s) for s in sources], payload['packet_sha256'],
            batch_routes.decode(proposals), payload['batch_review_sha256'], batch_routes.decode(plan),
            payload['planning_review_sha256'])
    result = link.reconcile(*args, current_ideas())
    return {'format':'openworkgraph.ai-implementation-portfolio-result.v1',
            'report_json':contract._json(result).decode('utf-8'),
            'reconciliation_sha256':result['reconciliation_sha256'],
            'portfolio_written':False, 'execution_authorized':False}


@router.post('/v1/ai-opportunity-handoff/planning-portfolio')
async def reconcile(request: Request):
    payload = await batch_routes.body(request)
    try:
        result = await run_in_threadpool(documents, payload)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Portfolio reconciliation refused; check the original planning pins and saved inventory') from None
    batch_routes.require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})
