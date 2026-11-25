"""Authenticated shared procurement endpoints; no service-token fallback."""
from fastapi import Depends,Path,Query,Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .process_procurement import SharedProcurement,Submit,Decision,Remove,Handoff
from shared.agent_procurement import MAX_BYTES


def add_routes(router,store,human,body,response):
    procurement=SharedProcurement(store)
    base="/v1/human/processes/{workflow_id}/procurement"
    ident=r"^[A-Za-z0-9_-]{1,80}$"

    @router.get("/process-procurement-client.js",include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name("process_procurement_client.js").read_text(encoding="utf-8"),
                        media_type="application/javascript",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

    @router.get("/v1/human/process-procurement-portfolio")
    def inventory(after:str=Query(default="",max_length=80),limit:int=Query(default=50,ge=1,le=100),p=Depends(human)):
        return response(procurement.inventory,p,after,limit)

    @router.get(base)
    def detail(workflow_id:str=Path(pattern=ident),revision:int|None=Query(default=None,ge=1,le=30),p=Depends(human)):
        return response(procurement.detail,p,workflow_id,revision)

    @router.post(base)
    async def submit(request:Request,workflow_id:str=Path(pattern=ident),p=Depends(human)):
        await run_in_threadpool(response,procurement.preflight,p,workflow_id,"submit")
        return await run_in_threadpool(response,procurement.submit,p,workflow_id,
                                       await body(request,Submit,maximum=MAX_BYTES*2+65536))

    @router.post(base+"/decision")
    async def decide(request:Request,workflow_id:str=Path(pattern=ident),p=Depends(human)):
        await run_in_threadpool(response,procurement.preflight,p,workflow_id,"review")
        return await run_in_threadpool(response,procurement.decide,p,workflow_id,
                                       await body(request,Decision,maximum=MAX_BYTES*2+65536))

    @router.post(base+"/check-handoff")
    async def check_handoff(request:Request,workflow_id:str=Path(pattern=ident),p=Depends(human)):
        await run_in_threadpool(response,store.require_scope,p,"org:process:read")
        return await run_in_threadpool(response,procurement.check_handoff,p,workflow_id,
                                       await body(request,Handoff,maximum=MAX_BYTES*2+65536))

    @router.post(base+"/delete")
    async def delete(request:Request,workflow_id:str=Path(pattern=ident),p=Depends(human)):
        await run_in_threadpool(response,procurement.preflight,p,workflow_id,"delete")
        return await run_in_threadpool(response,procurement.delete,p,workflow_id,await body(request,Remove,maximum=4096))
