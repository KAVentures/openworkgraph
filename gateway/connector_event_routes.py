"""Separate owner-only disclosure routes; worker token cannot read event payloads."""
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from .admin_accounts import current_admin
from .connector_job_routes import bearer, parse, reply
from .connector_event_publications import Publications, Approve, Upload, Remove, Bundle, bundle_csv
from shared.connector_event_snapshot import MAX_BYTES
from .analysis_publication_routes import retention_hooks
from shared.lifespan import extend_lifespan


def install(app,jobs,admin):
    store=Publications(jobs)
    start,stop=retention_hooks(store)
    extend_lifespan(app,startup=start,shutdown=stop)
    app.state.connector_event_publications=store
    router=APIRouter()
    base="/v1/admin/connector-events"

    def owner(auth,writing=False):
        principal=admin(auth,writing)
        identity=current_admin()
        if identity is None or not identity.is_owner():
            raise HTTPException(403,"Gateway owner approval required for connector event disclosures")
        return principal

    @router.get(base+"/{organization_id}")
    async def inventory(organization_id:str,authorization:str|None=Header(default=None)):
        owner(authorization)
        return await reply(store.inventory,organization_id)

    @router.post(base+"/{organization_id}/{job_id}/approve")
    async def approve(organization_id:str,job_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(store.approve,organization_id,job_id,await parse(request,Approve),principal)

    @router.post(base+"/{organization_id}/{job_id}/{grant_id}/delete")
    async def delete(organization_id:str,job_id:str,grant_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(store.remove,organization_id,job_id,await parse(request,Remove),principal,grant_id)

    @router.post(base+"/{organization_id}/export")
    async def export(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        spec=await parse(request,Bundle)
        result=await reply(store.export,organization_id,spec,principal)
        result.headers["Content-Disposition"]='attachment; filename="owg-connector-event-bundle.json"'
        return result

    @router.post(base+"/{organization_id}/export-csv")
    async def export_csv(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        spec=await parse(request,Bundle)
        result=await reply(store.export,organization_id,spec,principal)
        import json
        return Response(bundle_csv(json.loads(result.body)),media_type="text/csv",
                        headers={"Cache-Control":"no-store","Content-Disposition":'attachment; filename="owg-connector-events-machine-import.csv"',
                                 "X-Content-Type-Options":"nosniff"})

    @router.get("/v1/connector-worker/event-publication")
    async def preflight(authorization:str|None=Header(default=None)):
        return await reply(store.preflight,bearer(authorization))

    @router.post("/v1/connector-worker/event-publication")
    async def upload(request:Request,authorization:str|None=Header(default=None)):
        token=bearer(authorization)
        # Reject unapproved clients before accepting bounded sensitive bytes.
        await reply(store.preflight,token)
        return await reply(store.upload,token,await parse(request,Upload,maximum=MAX_BYTES+4096))

    @router.get("/admin/connector-events",include_in_schema=False)
    def page():
        from .html_pages import render_page
        return render_page("connector_events.html")

    app.include_router(router)
