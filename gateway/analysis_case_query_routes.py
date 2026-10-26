"""Queries remain human/case authorized on every read, write and export."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query as Parameter, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from .analysis_case_queries import Evaluate, Save, Remove
from .analysis_publication_routes import ID, body, response

QUERY_ID=r'^qry_[a-f0-9]{32}$'


def build_router(store,human):
    router=APIRouter();base='/v1/human/analyses/{view_id}/case-data'

    @router.get('/analysis-case-queries-client.js',include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('analysis_case_queries_client.js').read_text(encoding='utf-8'),media_type='application/javascript',
            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.post(base+'/query')
    async def evaluate(request:Request,view_id:str=Path(pattern=ID),parent_revision:int=Parameter(ge=1,le=10),p=Depends(human)):
        await run_in_threadpool(response,store.read_preflight,p,view_id,parent_revision)
        parsed=await body(request,Evaluate,16*1024)
        if parsed.parent_revision!=parent_revision:raise HTTPException(422,'Parent revision differs from the request')
        return await run_in_threadpool(response,store.evaluate,p,view_id,parsed)

    @router.get(base+'/queries')
    def inventory(view_id:str=Path(pattern=ID),parent_revision:int=Parameter(ge=1,le=10),p=Depends(human)):
        return response(store.inventory,p,view_id,parent_revision)

    @router.post(base+'/queries')
    async def save(request:Request,view_id:str=Path(pattern=ID),parent_revision:int=Parameter(ge=1,le=10),p=Depends(human)):
        await run_in_threadpool(response,store.preflight,p,view_id,parent_revision)
        parsed=await body(request,Save,16*1024)
        if parsed.parent_revision!=parent_revision:
            raise HTTPException(422,'Parent revision differs from the request')
        return await run_in_threadpool(response,store.save,p,view_id,parsed)

    @router.get(base+'/queries/{query_id}')
    def detail(view_id:str=Path(pattern=ID),query_id:str=Path(pattern=QUERY_ID),parent_revision:int=Parameter(ge=1,le=10),revision:int|None=Parameter(default=None,ge=1,le=10),p=Depends(human)):
        return response(store.detail,p,view_id,parent_revision,query_id,revision)

    @router.post(base+'/queries/{query_id}')
    async def update(request:Request,view_id:str=Path(pattern=ID),query_id:str=Path(pattern=QUERY_ID),parent_revision:int=Parameter(ge=1,le=10),p=Depends(human)):
        await run_in_threadpool(response,store.preflight,p,view_id,parent_revision)
        parsed=await body(request,Save,16*1024)
        if parsed.parent_revision!=parent_revision:
            raise HTTPException(422,'Parent revision differs from the request')
        return await run_in_threadpool(response,store.save,p,view_id,parsed,query_id)

    @router.get(base+'/queries/{query_id}/export')
    def export(view_id:str=Path(pattern=ID),query_id:str=Path(pattern=QUERY_ID),parent_revision:int=Parameter(ge=1,le=10),revision:int=Parameter(ge=1,le=10),p=Depends(human)):
        result=response(store.detail,p,view_id,parent_revision,query_id,revision,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-shared-case-analysis.json"'
        return result

    @router.post(base+'/queries/{query_id}/delete')
    async def delete(request:Request,view_id:str=Path(pattern=ID),query_id:str=Path(pattern=QUERY_ID),parent_revision:int=Parameter(ge=1,le=10),p=Depends(human)):
        await run_in_threadpool(response,store.preflight,p,view_id,parent_revision,owner=True)
        parsed=await body(request,Remove,16*1024)
        if parsed.parent_revision!=parent_revision:
            raise HTTPException(422,'Parent revision differs from the request')
        return await run_in_threadpool(response,store.delete,p,view_id,query_id,parsed)
    return router
