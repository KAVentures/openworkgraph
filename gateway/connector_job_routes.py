"""Named-admin and individually job-scoped worker routes."""
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool, StrictInt, ValidationError
from starlette.concurrency import run_in_threadpool
import json

from . import admin_accounts
from .auth import env_token_matches
from .connector_jobs import Jobs, JobError, Create, Change, Claim, Receipt, Strict
from shared.lifespan import extend_lifespan


class Rotate(Strict):
    expected_revision: StrictInt=Field(ge=1,le=1_000_000)
    confirmed: StrictBool


def bearer(raw):
    return raw[7:].strip() if raw and raw.lower().startswith("bearer ") else ""


def unique(pairs):
    out={}
    for k,v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k]=v
    return out


async def parse(request,model):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>8192:
            raise HTTPException(413,"connector job request too large")
    try:
        return model.model_validate(json.loads(data,object_pairs_hook=unique))
    except (ValueError,ValidationError,UnicodeError,RecursionError):
        raise HTTPException(422,"invalid connector job request") from None


async def reply(fn,*args):
    try:
        result=await run_in_threadpool(fn,*args)
    except JobError as exc:
        raise HTTPException(exc.status,exc.code) from None
    return JSONResponse(result,headers={"Cache-Control":"no-store"})


def install_connector_jobs(app,db,settings):
    jobs=Jobs(db)
    extend_lifespan(app,startup=jobs.initialize)
    app.state.connector_jobs=jobs
    router=APIRouter()
    def admin(raw,writing=False):
        if not env_token_matches(bearer(raw),settings.admin_token):
            raise HTTPException(401,"Gateway administrator sign-in required")
        identity=admin_accounts.current_admin()
        if identity is None:
            # This installer requires the existing enterprise identity middleware;
            # never grant implicit bootstrap authority when middleware is absent.
            raise HTTPException(401,"Gateway administrator identity required")
        if writing and not identity.can_write():
            raise HTTPException(403,"read-only administrator")
        return identity.audit_id

    @router.get("/v1/admin/connector-jobs/{organization_id}")
    async def inventory(organization_id:str,authorization:str|None=Header(default=None)):
        admin(authorization)
        return await reply(jobs.inventory,organization_id)

    @router.post("/v1/admin/connector-jobs")
    async def create(request:Request,authorization:str|None=Header(default=None)):
        principal=admin(authorization,True)
        return await reply(jobs.create,await parse(request,Create),principal)

    @router.get("/v1/admin/connector-jobs/{organization_id}/{job_id}/history")
    async def history(organization_id:str,job_id:str,limit:int=Query(default=50,ge=1,le=100),authorization:str|None=Header(default=None)):
        admin(authorization)
        return await reply(jobs.history,organization_id,job_id,limit)

    @router.post("/v1/admin/connector-jobs/{organization_id}/{job_id}/state")
    async def change(organization_id:str,job_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=admin(authorization,True)
        return await reply(jobs.change,organization_id,job_id,await parse(request,Change),principal)

    @router.post("/v1/admin/connector-jobs/{organization_id}/{job_id}/rotate")
    async def rotate(organization_id:str,job_id:str,request:Request,authorization:str|None=Header(default=None)):
        principal=admin(authorization,True)
        spec=await parse(request,Rotate)
        return await reply(jobs.rotate,organization_id,job_id,spec.expected_revision,spec.confirmed,principal)

    @router.post("/v1/connector-worker/claim")
    async def claim(request:Request,authorization:str|None=Header(default=None)):
        return await reply(jobs.claim,bearer(authorization),await parse(request,Claim))

    @router.post("/v1/connector-worker/complete")
    async def complete(request:Request,authorization:str|None=Header(default=None)):
        return await reply(jobs.complete,bearer(authorization),await parse(request,Receipt))

    @router.get("/admin/connector-jobs",include_in_schema=False)
    def page():
        from .html_pages import render_page
        return render_page("connector_jobs.html")

    app.include_router(router)
