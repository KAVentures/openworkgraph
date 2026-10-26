"""Reuse the process portal's human auth, parser and router seam."""
from fastapi import Depends, Path, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from shared.process_hierarchy import Save, Remove
from .process_hierarchies import ProcessHierarchies


def add_routes(router,store,human,body,response):
    maps=ProcessHierarchies(store);base='/v1/human/processes/{workflow_id}/hierarchy'
    ID=r'^[A-Za-z0-9_-]{1,80}$'

    @router.get('/process-hierarchy-client.js',include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('process_hierarchy_client.js').read_text(encoding='utf-8'),
            media_type='application/javascript',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get('/v1/human/process-hierarchies')
    def inventory(after:str=Query(default='',max_length=80),limit:int=Query(default=50,ge=1,le=100),p=Depends(human)):
        return response(maps.inventory,p,after,limit)

    @router.get(base)
    def detail(workflow_id:str=Path(pattern=ID),revision:int|None=Query(default=None,ge=1,le=20),p=Depends(human)):
        return response(maps.detail,p,workflow_id,revision)

    @router.post(base)
    async def save(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,maps.preflight,p,workflow_id,writing=True)
        return await run_in_threadpool(response,maps.save,p,workflow_id,await body(request,Save,maximum=64*1024))

    @router.post(base+'/delete')
    async def delete(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,maps.preflight,p,workflow_id,writing=True,owner=True)
        return await run_in_threadpool(response,maps.delete,p,workflow_id,await body(request,Remove,maximum=4096))

    @router.get(base+'/export')
    def export(workflow_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=20),p=Depends(human)):
        result=response(maps.detail,p,workflow_id,revision,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-process-repository-map.json"'
        return result
