"""Local human-only enterprise connector operations and REST feed routes."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import enterprise_rest_feed as rest
from . import google_workspace_audit as google
from . import enterprise_sql_feed as sql
from . import enterprise_connector_operations as operations
from .event_log_routes import _json_body
from .local_auth import dashboard_session_valid

router=APIRouter()
DASH=Path(__file__).resolve().parents[1]/"dashboard"
HEADERS={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}


def require(request):
    auth=request.headers.get("authorization","")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403,"Explicit local dashboard session required")


async def call(request,fn,*args,**kwargs):
    require(request)
    try:
        result=await run_in_threadpool(fn,*args,**kwargs)
    except rest.FeedError as error:
        raise HTTPException(error.status,error.code) from None
    require(request)
    return JSONResponse(result,headers=HEADERS)


@router.get("/enterprise-connectors")
def page():
    return FileResponse(DASH/"enterprise_connectors.html",media_type="text/html",headers={
        **HEADERS,"Content-Security-Policy":"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})


@router.get("/enterprise-connectors.js")
def script():
    return FileResponse(DASH/"enterprise_connectors.js",media_type="application/javascript",headers=HEADERS)


@router.get("/v1/enterprise-connectors")
async def inventory(request:Request,freshness_hours:int=Query(default=24,ge=1,le=720)):
    return await call(request,operations.inventory,freshness_hours)


@router.get("/v1/enterprise-connectors/rest")
async def rest_inventory(request:Request):
    return await call(request,rest.inventory)


@router.post("/v1/enterprise-connectors/rest")
async def rest_create(request:Request):
    require(request)
    body=await _json_body(request,"REST feed configuration",strict=True,limit=8192)
    return await call(request,rest.create,body)


@router.get("/v1/enterprise-connectors/sql/catalog")
async def sql_catalog(request:Request):
    return await call(request,sql.catalog)


@router.post("/v1/enterprise-connectors/sql")
async def sql_create(request:Request):
    require(request)
    body=await _json_body(request,"SQL feed configuration",strict=True,limit=4096)
    return await call(request,sql.create,body)


@router.post("/v1/enterprise-connectors/google")
async def google_create(request:Request):
    require(request)
    body=await _json_body(request,"Google Workspace audit profile",strict=True,limit=8192)
    return await call(request,google.create,body)


@router.post("/v1/enterprise-connectors/rest/{profile_id}/run")
async def rest_run(request:Request,profile_id:str):
    require(request)
    body=await _json_body(request,"REST feed run",strict=True,limit=1024)
    if not isinstance(body,dict) or set(body)!={"revision","confirm_reads","confirm_retention"}:
        raise HTTPException(422,"Exact revision and separate confirmations required")
    return await call(request,rest.run,profile_id,body["revision"],
                      confirm_reads=body["confirm_reads"],confirm_retention=body["confirm_retention"])


@router.post("/v1/enterprise-connectors/rest/{profile_id}/state")
async def rest_state(request:Request,profile_id:str):
    require(request)
    body=await _json_body(request,"REST feed state",strict=True,limit=1024)
    if not isinstance(body,dict) or set(body)!={"revision","enabled","confirm_change"} or body["confirm_change"] is not True:
        raise HTTPException(422,"Exact revision and explicit confirmation required")
    return await call(request,rest.change,profile_id,body["revision"],body["enabled"])


@router.post("/v1/enterprise-connectors/rest/{profile_id}/remove")
async def rest_remove(request:Request,profile_id:str):
    require(request)
    body=await _json_body(request,"REST feed removal",strict=True,limit=1024)
    if not isinstance(body,dict) or set(body)!={"revision","confirm_remove"} or body["confirm_remove"] is not True:
        raise HTTPException(422,"Exact revision and explicit removal confirmation required")
    return await call(request,rest.delete,profile_id,body["revision"])
