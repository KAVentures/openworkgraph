"""Authenticated catalog companion reads for existing evidence authoring."""
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from shared import ai_implementation_knowledge_context as ctx
from shared import ai_implementation_review as contract
from . import ai_implementation_batch_routes as batch

router = APIRouter()
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'ai_implementation_knowledge_context.js'


def catalog_page(after):
    from .local_process_catalog import knowledge, catalog
    page = knowledge.page(catalog.owner, after=after, limit=20)
    return {'format':'openworkgraph.ai-implementation-knowledge-list.v1',
            'processes':[{**ctx.reference(r), 'title':r['title']} for r in page['workflows'] if r.get('process_model')],
            'has_more':page['has_more'], 'next_after':page['next_after']}


def latest(key):
    from .local_process_catalog import catalog
    return catalog.get(key)


def documents(payload, listing=False):
    if listing:
        ctx.require(isinstance(payload, dict) and set(payload)=={'after'}, 'Exact cursor required')
        after=payload['after']
        ctx.require(isinstance(after,str) and (after=='' or ctx.ID.fullmatch(after)), 'Invalid catalog cursor')
        return catalog_page(after)
    ctx.require(isinstance(payload,dict) and set(payload)=={'sources','packet_sha256','max_bytes','references'}, 'Exact context inputs required')
    sources=payload['sources']
    ctx.require(isinstance(sources,list) and 1<=len(sources)<=contract.MAX_BUNDLES and all(isinstance(s,str) for s in sources), 'Original sources required')
    packet=contract.prepare([batch.decode(s) for s in sources],max_bytes=payload['max_bytes'])
    ctx.require(packet['packet_sha256']==payload['packet_sha256'] and packet['event_count']>0, 'Prepared evidence changed')
    result=ctx.build(packet['packet_sha256'],payload['references'],latest)
    return {'format':'openworkgraph.ai-implementation-knowledge-context-result.v1',
            'context_json':ctx.canonical(result).decode('utf-8'), 'context_sha256':result['context_sha256'],
            'packet_sha256':packet['packet_sha256'], 'execution_authorized':False, 'external_sharing_authorized':False}


async def process(request, listing=False):
    payload=await batch.body(request)
    try:
        result=await run_in_threadpool(documents,payload,listing)
    except (ValueError,TypeError,KeyError,AttributeError,LookupError,RecursionError,OverflowError):
        raise HTTPException(422,'Knowledge companion refused; reload the exact evidence and current saved process revisions') from None
    batch.require_dashboard(request)
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


@router.post('/v1/ai-opportunity-handoff/knowledge-list')
async def listing(request:Request):
    return await process(request,True)


@router.post('/v1/ai-opportunity-handoff/knowledge-context')
async def context(request:Request):
    return await process(request)


@router.get('/ai-implementation-knowledge-context.js')
def script():
    return FileResponse(SCRIPT,media_type='application/javascript')
