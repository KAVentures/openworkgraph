"""Read-only feedback handoff and revision review of existing v1 proposals."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import ai_implementation_improvement as improvement
from shared import ai_implementation_review as contract
from . import ai_implementation_batch_routes as batch_routes
from . import ai_implementation_forecast_routes as forecast_routes

router = APIRouter()
FIELDS = forecast_routes.FIELDS | {'forecast_review_sha256'}


def documents(payload, *, revising=False):
    fields = FIELDS | {'feedback_sha256','revised_proposal_json'} if revising else FIELDS
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError('Exact improvement inputs required')
    if revising:
        raw = payload['revised_proposal_json']
        if not isinstance(raw, str) or not 0 < len(raw.encode('utf-8')) <= improvement.MAX_BYTES:
            raise ValueError('Invalid revised proposal size')
        revised = batch_routes.decode(raw)
    verified = forecast_routes.documents({k:payload[k] for k in forecast_routes.FIELDS})
    report = batch_routes.decode(verified['report_json'])
    args = ([batch_routes.decode(s) for s in payload['sources']],
            batch_routes.decode(payload['proposals_json'])[payload['item_index']], report, payload['forecast_review_sha256'])
    if revising:
        result = improvement.revise(*args, payload['feedback_sha256'], revised)
        return {'format':'openworkgraph.ai-implementation-improvement-review-result.v1',
                'report_json':contract._json(result).decode('utf-8'), 'revision_review_sha256':result['revision_review_sha256'],
                'portfolio_written':False, 'execution_authorized':False}
    result = improvement.prepare(*args)
    return {'format':'openworkgraph.ai-implementation-improvement-feedback-result.v1',
            'feedback_json':contract._json(result).decode('utf-8'), 'feedback_sha256':result['feedback_sha256'],
            'portfolio_written':False, 'execution_authorized':False}


async def process(request, revising):
    payload = await batch_routes.body(request)
    try:
        result = await run_in_threadpool(documents, payload, revising=revising)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, ZeroDivisionError):
        raise HTTPException(422, 'Improvement handoff refused; refresh the original forecast, evidence and feedback pins') from None
    batch_routes.require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})


@router.post('/v1/ai-opportunity-handoff/improvement-feedback')
async def feedback(request: Request):
    return await process(request, False)


@router.post('/v1/ai-opportunity-handoff/improvement-revision')
async def revision(request: Request):
    return await process(request, True)
