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

    @router.post(base+"/{organization_id}/quality-review")
    async def quality_review(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_publication_quality import Review,review
        principal=owner(authorization,True)
        return await reply(review,store,organization_id,await parse(request,Review,maximum=32768),principal)

    @router.post(base+"/{organization_id}/quality-export")
    async def quality_export(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_publication_quality import Export,export
        principal=owner(authorization,True)
        result=await reply(export,store,organization_id,await parse(request,Export,maximum=32768),principal)
        result.headers["Content-Disposition"]='attachment; filename="owg-quality-reviewed-event-bundle.json"'
        return result

    @router.post(base+"/{organization_id}/quality-export-csv")
    async def quality_export_csv(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_publication_quality import Export,export
        import json
        principal=owner(authorization,True)
        result=await reply(export,store,organization_id,await parse(request,Export,maximum=32768),principal)
        data=json.loads(result.body)
        return Response(bundle_csv(data),media_type="text/csv",headers={
            "Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
            "X-OWG-Quality-SHA256":data["quality_review"]["quality_sha256"],
            "Content-Disposition":'attachment; filename="owg-quality-reviewed-machine-import.csv"'})

    @router.get("/admin/connector-publication-quality",include_in_schema=False)
    def quality_page():
        from .html_pages import render_page
        return render_page("connector_publication_quality.html")

    @router.get("/admin/connector-events",include_in_schema=False)
    def page():
        from .html_pages import render_page
        return render_page("connector_events.html")

    from .connector_case_review import ReviewedCases, Catalog, Plan, Export, linked_csv, MAX_REQUEST
    reviewed=ReviewedCases(store)
    app.state.connector_case_review=reviewed

    @router.post(base+"/{organization_id}/case-catalog")
    async def case_catalog(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(reviewed.catalog,organization_id,await parse(request,Catalog),principal)

    @router.post(base+"/{organization_id}/case-review")
    async def case_review(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(reviewed.preview,organization_id,await parse(request,Plan,maximum=MAX_REQUEST),principal)

    @router.post(base+"/{organization_id}/case-temporal-review")
    async def case_temporal_review(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_temporal_review import Alignment,temporal_review
        principal=owner(authorization,True)
        return await reply(temporal_review,reviewed,organization_id,await parse(request,Alignment,maximum=MAX_REQUEST),principal)

    @router.post(base+"/{organization_id}/case-conformance-catalog")
    async def case_conformance_catalog(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_linked_conformance import Catalog,catalog
        principal=owner(authorization,True)
        return await reply(catalog,reviewed,organization_id,await parse(request,Catalog,maximum=MAX_REQUEST+4096),principal)

    @router.post(base+"/{organization_id}/case-conformance-review")
    async def case_conformance_review(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_linked_conformance import Review,review
        principal=owner(authorization,True)
        return await reply(review,reviewed,organization_id,await parse(request,Review,maximum=MAX_REQUEST+65536),principal)

    @router.post(base+"/{organization_id}/case-conformance-worklist")
    async def case_conformance_worklist(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_linked_conformance import Worklist,worklist,worklist_csv
        import json
        principal=owner(authorization,True)
        result=await reply(worklist,reviewed,organization_id,await parse(request,Worklist,maximum=MAX_REQUEST+65536),principal)
        data=json.loads(result.body)
        return Response(worklist_csv(data),media_type="text/csv",headers={
            "Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
            "X-OWG-Conformance-SHA256":data["conformance_sha256"],
            "Content-Disposition":'attachment; filename="owg-linked-conformance-exceptions.csv"'})

    from .connector_exception_triage import Queues, Save as QueueSave, Page as QueuePage, Change as QueueChange, Delete as QueueDelete
    queues=Queues(reviewed)
    queue_start,queue_stop=retention_hooks(queues)
    extend_lifespan(app,startup=queue_start,shutdown=queue_stop)
    app.state.connector_exception_queues=queues

    @router.get(base+"/{organization_id}/exception-queues")
    async def exception_queues(organization_id:str,authorization:str|None=Header(default=None)):
        owner(authorization)
        return await reply(queues.inventory,organization_id)

    @router.post(base+"/{organization_id}/exception-queues")
    async def save_exception_queue(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(queues.save,organization_id,await parse(request,QueueSave,maximum=MAX_REQUEST+65536),principal)

    @router.post(base+"/{organization_id}/exception-queues/{queue_id}/page")
    async def exception_queue_page(organization_id:str,queue_id:str,request:Request,authorization:str|None=Header(default=None)):
        owner(authorization)
        return await reply(queues.page,organization_id,queue_id,await parse(request,QueuePage))

    @router.post(base+"/{organization_id}/exception-queues/{queue_id}/change")
    async def change_exception_queue(organization_id:str,queue_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(queues.change,organization_id,queue_id,await parse(request,QueueChange,maximum=32768),principal)

    @router.post(base+"/{organization_id}/exception-queues/{queue_id}/export")
    async def export_exception_queue(organization_id:str,queue_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_exception_triage import Export,disposition_csv
        import json
        principal=owner(authorization,True)
        result=await reply(queues.export,organization_id,queue_id,await parse(request,Export),principal)
        data=json.loads(result.body)
        return Response(disposition_csv(data),media_type="text/csv",headers={
            "Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
            "X-OWG-Disposition-SHA256":data["export_sha256"],
            "Content-Disposition":'attachment; filename="owg-evidence-dispositions.csv"'})

    @router.post(base+"/{organization_id}/exception-queues/{queue_id}/delete")
    async def delete_exception_queue(organization_id:str,queue_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(queues.delete,organization_id,queue_id,await parse(request,QueueDelete),principal)

    @router.get("/admin/connector-exception-triage",include_in_schema=False)
    def exception_page():
        from .html_pages import render_page
        return render_page("connector_exception_triage.html")

    @router.post(base+"/{organization_id}/case-quality-export")
    async def case_quality_export(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_publication_quality import LinkedExport,linked_export
        principal=owner(authorization,True)
        result=await reply(linked_export,reviewed,organization_id,await parse(request,LinkedExport,maximum=MAX_REQUEST+32768),principal)
        result.headers["Content-Disposition"]='attachment; filename="owg-quality-reviewed-linked-cases.json"'
        return result

    @router.post(base+"/{organization_id}/case-export-portable")
    async def case_export_portable(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_portable_linkage import PortableExport,export_portable
        principal=owner(authorization,True)
        result=await reply(export_portable,reviewed,organization_id,await parse(request,PortableExport,maximum=MAX_REQUEST+256*1024),principal)
        result.headers["Content-Disposition"]='attachment; filename="owg-portable-reviewed-linked-evidence.json"'
        return result

    @router.post(base+"/{organization_id}/case-export")
    async def case_export(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        result=await reply(reviewed.export,organization_id,await parse(request,Export,maximum=MAX_REQUEST),principal)
        result.headers["Content-Disposition"]='attachment; filename="owg-reviewed-cross-source-cases.json"'
        return result

    @router.post(base+"/{organization_id}/case-export-csv")
    async def case_export_csv(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        result=await reply(reviewed.export,organization_id,await parse(request,Export,maximum=MAX_REQUEST),principal)
        import json
        return Response(linked_csv(json.loads(result.body)),media_type="text/csv",
                        headers={"Cache-Control":"no-store","Content-Disposition":'attachment; filename="owg-reviewed-cross-source-machine-import.csv"',
                                 "X-Content-Type-Options":"nosniff"})

    @router.get("/admin/connector-case-review",include_in_schema=False)
    def case_page():
        from .html_pages import render_page
        return render_page("connector_case_review.html")

    from .connector_crosswalk_proposals import Propose, propose, MAX_REQUEST as CROSSWALK_MAX
    from .connector_review_plans import SavedPlans, Save, Delete
    saved=SavedPlans(reviewed)
    saved_start,saved_stop=retention_hooks(saved)
    extend_lifespan(app,startup=saved_start,shutdown=saved_stop)
    app.state.connector_review_plans=saved

    @router.post(base+"/{organization_id}/crosswalk-proposals")
    async def crosswalk_proposals(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(propose,reviewed,organization_id,await parse(request,Propose,maximum=CROSSWALK_MAX),principal)

    @router.get(base+"/{organization_id}/saved-reviews")
    async def saved_reviews(organization_id:str,authorization:str|None=Header(default=None)):
        owner(authorization)
        return await reply(saved.inventory,organization_id)

    @router.post(base+"/{organization_id}/saved-reviews")
    async def save_review(organization_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(saved.save,organization_id,await parse(request,Save,maximum=MAX_REQUEST+4096),principal)

    @router.get(base+"/{organization_id}/saved-reviews/{plan_id}/{revision}")
    async def read_review(organization_id:str,plan_id:str,revision:int,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(saved.read,organization_id,plan_id,revision,principal)

    @router.post(base+"/{organization_id}/saved-reviews/{plan_id}/delete")
    async def delete_review(organization_id:str,plan_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(saved.delete,organization_id,plan_id,await parse(request,Delete),principal)

    @router.get("/admin/connector-crosswalks",include_in_schema=False)
    def crosswalk_page():
        from .html_pages import render_page
        return render_page("connector_crosswalks.html")

    # Specific saved-review and exception-queue delete routes must be registered
    # before the generic job/grant deletion pattern.
    @router.get(base+"/{organization_id}/exception-queues/{queue_id}/followups")
    async def exception_followups(organization_id:str,queue_id:str,authorization:str|None=Header(default=None)):
        from .connector_exception_followup import report
        owner(authorization)
        return await reply(report,queues,organization_id,queue_id)

    @router.post(base+"/{organization_id}/exception-queues/{queue_id}/followups/change")
    async def change_exception_followups(organization_id:str,queue_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_exception_followup import Change,change
        principal=owner(authorization,True)
        return await reply(change,queues,organization_id,queue_id,await parse(request,Change,maximum=32768),principal)

    @router.post(base+"/{organization_id}/exception-queues/{queue_id}/followups/export")
    async def export_exception_followups(organization_id:str,queue_id:str,request:Request,authorization:str|None=Header(default=None)):
        from .connector_exception_followup import Export,export
        principal=owner(authorization,True)
        result=await reply(export,queues,organization_id,queue_id,await parse(request,Export),principal)
        result.headers["Content-Disposition"]='attachment; filename="owg-evidence-followups.json"'
        return result

    @router.post(base+"/{organization_id}/{job_id}/{grant_id}/delete")
    async def delete(organization_id:str,job_id:str,grant_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=owner(authorization,True)
        return await reply(store.remove,organization_id,job_id,await parse(request,Remove),principal,grant_id)

    app.include_router(router)
