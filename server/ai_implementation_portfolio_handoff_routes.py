"""Explicit single-idea scenario handoff through existing planning governance."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import ai_implementation_batch as batch
from shared import ai_implementation_planning as planning
from shared import ai_implementation_review as contract
from shared import ai_implementation_portfolio_handoff as handoff
from . import ai_implementation_batch_routes as batch_routes
from . import ai_implementation_portfolio_routes as portfolio_routes

router = APIRouter()
FIELDS = portfolio_routes.FIELDS | {'reconciliation_sha256','item_index','budget_estimate'}


def _args(payload, committing):
    fields = FIELDS | {'expected_preview_sha256','confirm_save_planning'} if committing else FIELDS
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError('Exact planning handoff inputs required')
    if committing and payload['confirm_save_planning'] is not True:
        raise ValueError('Explicit planning confirmation required')
    sources, proposals, plan = (payload[k] for k in ('sources','proposals_json','plan_json'))
    if (not isinstance(sources, list) or not 1 <= len(sources) <= contract.MAX_BUNDLES
            or not all(isinstance(s, str) for s in sources)
            or not isinstance(proposals, str) or not 0 < len(proposals.encode('utf-8')) <= batch.MAX_BATCH_BYTES
            or not isinstance(plan, str) or not 0 < len(plan.encode('utf-8')) <= planning.MAX_PLAN_BYTES):
        raise ValueError('Invalid source, proposal or planning size')
    return ([batch_routes.decode(s) for s in sources], payload['packet_sha256'],
            batch_routes.decode(proposals), payload['batch_review_sha256'], batch_routes.decode(plan),
            payload['planning_review_sha256'])


def _preview(payload, args, ideas):
    return handoff.preview(args, ideas, payload['reconciliation_sha256'], payload['item_index'], payload['budget_estimate'])


def _pipeline():
    from . import automation_pipeline
    return automation_pipeline


def operation(request, payload, committing=False):
    args = _args(payload, committing)
    batch_routes.require_dashboard(request)
    if not committing:
        return _preview(payload, args, portfolio_routes.current_ideas())
    pipeline = _pipeline()
    # Same process lock as existing portfolio writes, and existing plan() revision and graph checks.
    with pipeline._LOCK:
        preview = _preview(payload, args, pipeline.pipeline()['ideas'])
        if preview['preview_sha256'] != payload['expected_preview_sha256']:
            raise ValueError('Planning preview changed')
        batch_routes.require_dashboard(request)
        saved = pipeline.plan(preview['idea_id'], preview['proposed_planning'])
    return {'format':'openworkgraph.ai-implementation-portfolio-plan-saved.v1',
            'idea_id':saved['idea_id'], 'revision':saved['revision'], 'state':saved['state'],
            'preview_sha256':preview['preview_sha256'], 'portfolio_written':True,
            'assessment_changed':False, 'approval_changed':False, 'execution_authorized':False}


async def process(request, committing):
    payload = await batch_routes.body(request)
    try:
        result = await run_in_threadpool(operation, request, payload, committing)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Planning handoff refused; refresh the portfolio and inspect the exact review and preview') from None
    batch_routes.require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})


@router.post('/v1/ai-opportunity-handoff/planning-save-preview')
async def preview(request: Request):
    return await process(request, False)


@router.post('/v1/ai-opportunity-handoff/planning-save-confirm')
async def confirm(request: Request):
    return await process(request, True)
