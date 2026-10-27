"""Implementation planning uses current human process roles and explicit declarations."""
from fastapi import Depends,Path,Query,Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from shared.process_implementation_plan import SaveBaseline,SaveReports,Freeze,Remove
from .process_implementation_plans import ImplementationPlans


def add_routes(router,store,human,body,response):
    plans=ImplementationPlans(store);base='/v1/human/processes/{workflow_id}/implementation-plan';ID=r'^[A-Za-z0-9_-]{1,80}$'

    @router.get('/process-implementation-client.js',include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('process_implementation_client.js').read_text(encoding='utf-8'),media_type='application/javascript',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get(base)
    def detail(workflow_id:str=Path(pattern=ID),revision:int|None=Query(default=None,ge=1,le=50),p=Depends(human)):
        return response(plans.detail,p,workflow_id,revision)

    @router.post(base)
    async def save(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,plans.opportunities.preflight,p,workflow_id)
        return await run_in_threadpool(response,plans.save_baseline,p,workflow_id,await body(request,SaveBaseline,maximum=64*1024))

    @router.post(base+'/reports')
    async def reports(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,plans.opportunities.preflight,p,workflow_id)
        return await run_in_threadpool(response,plans.save_reports,p,workflow_id,await body(request,SaveReports,maximum=64*1024))

    @router.post(base+'/freeze')
    async def freeze(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,plans.opportunities.preflight,p,workflow_id,reviewing=True)
        return await run_in_threadpool(response,plans.freeze,p,workflow_id,await body(request,Freeze,maximum=4096))

    @router.post(base+'/delete')
    async def delete(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,plans.opportunities.preflight,p,workflow_id,deleting=True)
        return await run_in_threadpool(response,plans.delete,p,workflow_id,await body(request,Remove,maximum=4096))

    @router.get(base+'/export')
    def export(workflow_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=50),p=Depends(human)):
        result=response(plans.detail,p,workflow_id,revision,exporting=True);result.headers['Content-Disposition']='attachment; filename="owg-implementation-phase-plan.json"';return result
