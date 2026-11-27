"""Local human-only enterprise connector operations and REST feed routes."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import enterprise_rest_feed as rest
from . import google_workspace_audit as google
from . import slack_native_feed as slack
from . import salesforce_history_feed as sf
from . import servicenow_operations_feed as sn
from . import sap_procurement_feed as sap
from . import dataverse_audit_feed as dv
from . import hubspot_history_feed as hs
from . import databricks_event_feed as dbx
from . import snowflake_event_feed as snow
from . import netsuite_event_feed as ns
from . import workday_raas_feed as wd
from . import oracle_fusion_procurement_feed as oracle
from . import bigquery_event_feed as bq
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


@router.get("/v1/enterprise-connectors/bigquery/catalog")
async def bigquery_catalog(request:Request):
    return await call(request,bq.catalog_public)


@router.post("/v1/enterprise-connectors/bigquery")
async def bigquery_create(request:Request):
    require(request)
    body=await _json_body(request,"BigQuery approved view profile",strict=True,limit=4096)
    return await call(request,bq.create,body)


@router.get("/v1/enterprise-connectors/oracle_fusion/catalog")
async def oracle_fusion_catalog(request:Request):
    return await call(request,oracle.catalog_public)


@router.post("/v1/enterprise-connectors/oracle_fusion")
async def oracle_fusion_create(request:Request):
    require(request)
    body=await _json_body(request,"Oracle Fusion procurement profile",strict=True,limit=8192)
    return await call(request,oracle.create,body)


@router.get("/v1/enterprise-connectors/workday/catalog")
async def workday_catalog(request:Request):
    return await call(request,wd.catalog_public)


@router.post("/v1/enterprise-connectors/workday")
async def workday_create(request:Request):
    require(request)
    body=await _json_body(request,"Workday approved report profile",strict=True,limit=4096)
    return await call(request,wd.create,body)


@router.get("/v1/enterprise-connectors/netsuite/catalog")
async def netsuite_catalog(request:Request):
    return await call(request,ns.catalog_public)


@router.post("/v1/enterprise-connectors/netsuite")
async def netsuite_create(request:Request):
    require(request)
    body=await _json_body(request,"NetSuite approved record profile",strict=True,limit=4096)
    return await call(request,ns.create,body)


@router.get("/v1/enterprise-connectors/snowflake/catalog")
async def snowflake_catalog(request:Request):
    return await call(request,snow.catalog_public)


@router.post("/v1/enterprise-connectors/snowflake")
async def snowflake_create(request:Request):
    require(request)
    body=await _json_body(request,"Snowflake approved view profile",strict=True,limit=4096)
    return await call(request,snow.create,body)


@router.get("/v1/enterprise-connectors/databricks/catalog")
async def databricks_catalog(request:Request):
    return await call(request,dbx.catalog_public)


@router.post("/v1/enterprise-connectors/databricks")
async def databricks_create(request:Request):
    require(request)
    body=await _json_body(request,"Databricks approved view profile",strict=True,limit=4096)
    return await call(request,dbx.create,body)


@router.get("/v1/enterprise-connectors/hubspot/catalog")
async def hubspot_catalog(request:Request):
    return await call(request,hs.catalog_public)


@router.post("/v1/enterprise-connectors/hubspot")
async def hubspot_create(request:Request):
    require(request)
    body=await _json_body(request,"HubSpot history profile",strict=True,limit=32768)
    return await call(request,hs.create,body)


@router.get("/v1/enterprise-connectors/dataverse/catalog")
async def dataverse_catalog(request:Request):
    return await call(request,dv.catalog_public)


@router.post("/v1/enterprise-connectors/dataverse")
async def dataverse_create(request:Request):
    require(request)
    body=await _json_body(request,"Dataverse audit profile",strict=True,limit=65536)
    return await call(request,dv.create,body)


@router.get("/v1/enterprise-connectors/sap/catalog")
async def sap_catalog(request:Request):
    return await call(request,sap.catalog_public)


@router.post("/v1/enterprise-connectors/sap")
async def sap_create(request:Request):
    require(request)
    body=await _json_body(request,"SAP procurement profile",strict=True,limit=16384)
    return await call(request,sap.create,body)


@router.post("/v1/enterprise-connectors/servicenow")
async def servicenow_create(request:Request):
    require(request)
    body=await _json_body(request,"ServiceNow operations profile",strict=True,limit=65536)
    return await call(request,sn.create,body)


@router.post("/v1/enterprise-connectors/salesforce")
async def salesforce_create(request:Request):
    require(request)
    body=await _json_body(request,"Salesforce history profile",strict=True,limit=32768)
    return await call(request,sf.create,body)


@router.post("/v1/enterprise-connectors/slack")
async def slack_create(request:Request):
    require(request)
    body=await _json_body(request,"Slack native feed profile",strict=True,limit=8192)
    return await call(request,slack.create,body)


@router.post("/v1/enterprise-connectors/google")
async def google_create(request:Request):
    require(request)
    body=await _json_body(request,"Google Workspace audit profile",strict=True,limit=8192)
    return await call(request,google.create,body)


@router.post("/v1/enterprise-connectors/rest/{profile_id}/rotate-credential")
async def rotate_credential(request:Request,profile_id:str):
    from .enterprise_connector_rotation import rotate
    require(request)
    body=await _json_body(request,"Reviewed connector credential rotation",strict=True,limit=1024)
    return await call(request,rotate,profile_id,body)


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


@router.get("/v1/enterprise-connectors/tuning")
async def tuning_inventory(request:Request):
    from . import enterprise_ingestion_tuning as tuning
    return await call(request,tuning.inventory)


@router.post("/v1/enterprise-connectors/tuning/preview")
async def tuning_preview(request:Request):
    from . import enterprise_ingestion_tuning as tuning
    require(request)
    body=await _json_body(request,"Reviewed connector tuning preview",strict=True,limit=16384)
    return await call(request,tuning.preview,body)


@router.post("/v1/enterprise-connectors/tuning/apply")
async def tuning_apply(request:Request):
    from . import enterprise_ingestion_tuning as tuning
    require(request)
    body=await _json_body(request,"Reviewed connector tuning apply",strict=True,limit=16384)
    return await call(request,tuning.apply,body)


@router.get("/v1/enterprise-connectors/run-history")
async def ingestion_run_history(request:Request,profile_id:str|None=None,
                                days:int=Query(default=30,ge=1,le=90),
                                limit:int=Query(default=200,ge=1,le=1000),
                                state:str|None=None):
    from . import enterprise_ingestion_run_history as history
    return await call(request,history.history,profile_id=profile_id,days=days,limit=limit,state=state)


@router.get("/v1/enterprise-connectors/onboarding")
async def onboarding_readiness(request:Request,support_only:bool=Query(default=False)):
    from . import enterprise_source_onboarding as onboarding
    return await call(request,onboarding.inspect,support_only=support_only)


@router.get("/v1/enterprise-connectors/integrity")
async def retained_integrity_inventory(request:Request):
    from . import enterprise_retained_integrity as integrity
    return await call(request,integrity.inventory)


@router.post("/v1/enterprise-connectors/integrity/preview")
async def retained_integrity_preview(request:Request,action:str=Query(default="hold")):
    from . import enterprise_retained_integrity as integrity
    require(request)
    body=await _json_body(request,"Retained evidence integrity review",strict=True,limit=4096)
    return await call(request,integrity.inspect,body,action=action)


@router.post("/v1/enterprise-connectors/integrity/apply")
async def retained_integrity_apply(request:Request,action:str=Query(default="hold")):
    from . import enterprise_retained_integrity as integrity
    require(request)
    body=await _json_body(request,"Reviewed retained evidence containment",strict=True,limit=4096)
    return await call(request,integrity.apply,body,action=action)
