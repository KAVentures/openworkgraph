"""Case disclosure routes use the existing analysis identity and fresh case authorization."""
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from .analysis_case_data import Attach, CaseAccess, CaseDelete
from .analysis_publication_routes import ID, body, response


def build_router(store,human):
    router=APIRouter()
    base='/v1/human/analyses/{view_id}/case-data'

    @router.get('/analysis-cases-client.js',include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('analysis_cases_client.js').read_text(encoding='utf-8'),media_type='application/javascript',
            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.post(base)
    async def attach(request:Request,view_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,store.preflight,p,view_id)
        return await run_in_threadpool(response,store.attach,p,view_id,await body(request,Attach,640*1024))

    @router.get(base)
    def query(view_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=10),activity:str=Query(default='',pattern=r'^$|^A[1-9][0-9]{0,2}$'),
              variant:str=Query(default='',pattern=r'^$|^[a-f0-9]{64}$'),after:int=Query(default=0,ge=0,le=500),limit:int=Query(default=50,ge=1,le=100),p=Depends(human)):
        return response(store.read,p,view_id,revision,activity=activity,variant=variant,after=after,limit=limit)

    @router.get(base+'/cases/{case_code}')
    def case(view_id:str=Path(pattern=ID),case_code:str=Path(pattern=r'^C[1-9][0-9]{0,2}$'),revision:int=Query(ge=1,le=10),p=Depends(human)):
        return response(store.read,p,view_id,revision,case_code=case_code)

    @router.get(base+'/access')
    def access(view_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=10),p=Depends(human)):
        return response(store.access,p,view_id,revision)

    @router.post(base+'/access')
    async def change(request:Request,view_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,store.preflight,p,view_id)
        parsed=await body(request,CaseAccess)
        return await run_in_threadpool(response,store.access,p,view_id,parsed.revision,parsed)

    @router.post(base+'/delete')
    async def delete(request:Request,view_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,store.preflight,p,view_id)
        return await run_in_threadpool(response,store.delete,p,view_id,await body(request,CaseDelete))

    @router.get(base+'/export')
    def export(view_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=10),p=Depends(human)):
        result=response(store.read,p,view_id,revision,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-shared-case-data.json"'
        return result
    return router
