"""Human process roles, strict parser and bounded dependency manifests."""
from fastapi import Depends, Path, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from shared.process_dependencies import Save, Remove
from .process_dependencies import ProcessDependencies


def add_routes(router,store,human,body,response):
    dependencies=ProcessDependencies(store);base='/v1/human/processes/{workflow_id}/dependencies';ID=r'^[A-Za-z0-9_-]{1,80}$'

    @router.get('/process-dependency-client.js',include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('process_dependency_client.js').read_text(encoding='utf-8'),media_type='application/javascript',
            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get('/v1/human/process-dependency-graph')
    def graph(focus:str|None=Query(default=None,pattern=ID),p=Depends(human)):
        return response(dependencies.graph,p,focus)

    @router.get('/v1/human/process-dependency-graph/export')
    def graph_export(fingerprint:str=Query(pattern=r'^[a-f0-9]{64}$'),focus:str|None=Query(default=None,pattern=ID),p=Depends(human)):
        result=response(dependencies.graph,p,focus,expected_fingerprint=fingerprint,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-process-dependency-graph.json"'
        return result

    @router.get(base)
    def detail(workflow_id:str=Path(pattern=ID),revision:int|None=Query(default=None,ge=1,le=20),p=Depends(human)):
        return response(dependencies.detail,p,workflow_id,revision)

    @router.post(base)
    async def save(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,dependencies.catalog.preflight,p,workflow_id,writing=True)
        return await run_in_threadpool(response,dependencies.save,p,workflow_id,await body(request,Save,maximum=64*1024))

    @router.post(base+'/delete')
    async def delete(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,dependencies.catalog.preflight,p,workflow_id,writing=True,owner=True)
        return await run_in_threadpool(response,dependencies.delete,p,workflow_id,await body(request,Remove,maximum=4096))

    @router.get(base+'/export')
    def export(workflow_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=20),p=Depends(human)):
        result=response(dependencies.detail,p,workflow_id,revision,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-process-dependencies.json"'
        return result
