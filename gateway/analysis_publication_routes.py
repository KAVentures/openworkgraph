"""Human-only snapshot publication; delegated or analysis-session identity, never process/service sessions."""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .analysis_publications import Access, Delete, Publish, PublicationError
from .process_collaboration_routes import _object, _invalid_constant

ID = r'^ana_[a-f0-9]{32}$'


async def body(request, model):
    if request.headers.get('content-type','').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415,'application/json required')
    data = bytearray()
    try:
        async for part in request.stream():
            if len(data)+len(part)>160*1024: raise HTTPException(413,'Analysis request exceeds 160 KiB')
            data.extend(part)
        return model.model_validate(json.loads(data,object_pairs_hook=_object,parse_constant=_invalid_constant))
    except (ValueError,ValidationError,UnicodeError,RecursionError):
        raise HTTPException(422,'Invalid analysis snapshot or publication fields') from None
    finally:
        data.clear()


def response(operation,*args,**kwargs):
    try:return JSONResponse(operation(*args,**kwargs),headers={'Cache-Control':'no-store'})
    except PublicationError as exc:raise HTTPException(exc.status,str(exc)) from None


def build_router(store,human):
    router=APIRouter()

    @router.get('/analyses',include_in_schema=False)
    def portal():
        from .html_pages import render_page
        return render_page('analyses.html',config={})

    @router.get('/analysis-snapshot-client.js',include_in_schema=False)
    def client_script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('analysis_snapshot_client.js').read_text(encoding='utf-8'),
                        media_type='application/javascript',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get('/v1/human/analyses')
    def inventory(after: str=Query(default='',max_length=36),limit: int=Query(default=20,ge=1,le=100),p=Depends(human)):
        return response(store.inventory,p,after,limit)

    @router.post('/v1/human/analyses')
    async def publish(request:Request,p=Depends(human)):
        for scope in ('org:analysis:read','org:analysis:write'):
            if scope not in p.scopes:raise HTTPException(403,'Missing analysis permission: '+scope)
        parsed=await body(request,Publish)
        return await run_in_threadpool(response,store.publish,p,parsed)

    @router.get('/v1/human/analyses/{view_id}')
    def detail(view_id:str=Path(pattern=ID),revision:int|None=Query(default=None,ge=1,le=10),p=Depends(human)):
        return response(store.detail,p,view_id,revision)

    @router.post('/v1/human/analyses/{view_id}')
    async def update(request:Request,view_id:str=Path(pattern=ID),p=Depends(human)):
        # Recheck row access in the store after bounded parsing.
        await run_in_threadpool(response,store.preflight,p,view_id)
        return await run_in_threadpool(response,store.publish,p,await body(request,Publish),view_id)

    @router.get('/v1/human/analyses/{view_id}/access')
    def access(view_id:str=Path(pattern=ID),p=Depends(human)):
        return response(store.access,p,view_id)

    @router.post('/v1/human/analyses/{view_id}/access')
    async def set_access(request:Request,view_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,store.preflight,p,view_id,True)
        return await run_in_threadpool(response,store.access,p,view_id,await body(request,Access))

    @router.post('/v1/human/analyses/{view_id}/delete')
    async def delete(request:Request,view_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,store.preflight,p,view_id,True)
        return await run_in_threadpool(response,store.delete,p,view_id,await body(request,Delete))

    @router.get('/v1/human/analyses/{view_id}/export')
    def export(view_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=10),p=Depends(human)):
        result=response(store.detail,p,view_id,revision,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-shared-analysis.json"'
        return result

    return router


def retention_hooks(store):
    task = None
    stopped = None
    async def sweep():
        while not stopped.is_set():
            try:
                await run_in_threadpool(store.purge_expired)
                store.retention_ok = True
            except Exception:
                # No payloads, SQL or credentials are logged. Retry next tick.
                store.retention_ok = False
            if not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=60)
                except TimeoutError:
                    pass
    async def start():
        nonlocal task, stopped
        await run_in_threadpool(store.initialize)
        stopped = asyncio.Event()
        task = asyncio.create_task(sweep())
    async def stop():
        if task is not None:
            # Drain an in-flight DB operation before the app closes its pool.
            stopped.set()
            await task
    return start, stop
