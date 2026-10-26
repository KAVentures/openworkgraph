"""Reuse the existing process portal's human authentication and bounded parser."""
from fastapi import Depends, Path, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from shared.process_opportunity_review import Save, Transition, Remove
from .process_opportunity_review import SharedOpportunityStore


def add_routes(router,store,human,body,response):
    opportunities=SharedOpportunityStore(store);base='/v1/human/processes/{workflow_id}/opportunity'
    ID=r'^[A-Za-z0-9_-]{1,80}$'

    @router.get('/process-opportunity-client.js',include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('process_opportunity_client.js').read_text(encoding='utf-8'),media_type='application/javascript',
            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get('/v1/human/process-opportunity-pipeline')
    def inventory(after:str=Query(default='',max_length=80),limit:int=Query(default=50,ge=1,le=100),p=Depends(human)):
        return response(opportunities.inventory,p,after,limit)

    @router.get(base)
    def detail(workflow_id:str=Path(pattern=ID),revision:int|None=Query(default=None,ge=1,le=50),p=Depends(human)):
        return response(opportunities.detail,p,workflow_id,revision)

    @router.post(base)
    async def save(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,opportunities.preflight,p,workflow_id)
        return await run_in_threadpool(response,opportunities.save,p,workflow_id,await body(request,Save,maximum=64*1024))

    @router.post(base+'/transition')
    async def transition(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,opportunities.preflight,p,workflow_id,reviewing=True)
        return await run_in_threadpool(response,opportunities.transition,p,workflow_id,await body(request,Transition,maximum=4096))

    @router.get(base+'/export')
    def export(workflow_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=50),p=Depends(human)):
        result=response(opportunities.detail,p,workflow_id,revision,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-shared-opportunity.json"'
        return result

    @router.post(base+'/delete')
    async def delete(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,opportunities.preflight,p,workflow_id,deleting=True)
        return await run_in_threadpool(response,opportunities.delete,p,workflow_id,await body(request,Remove,maximum=4096))
