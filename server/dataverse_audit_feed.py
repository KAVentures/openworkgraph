"""Dataverse audit metadata via an explicitly approved application user.

Tenant-specific client credentials, fixed Web API origin, WhoAmI binding,
bounded paging and exact retained source identity. No changedata or writes.
"""
from datetime import timedelta
import hmac
import os
import re
import time
from urllib.parse import urlsplit, parse_qsl
from typing import Literal

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator
from . import enterprise_rest_feed as rest

TABLES=("account","contact","lead","opportunity","incident","quote","salesorder","invoice","task","appointment")
Table=Literal["account","contact","lead","opportunity","incident","quote","salesorder","invoice","task","appointment"]
GUID=re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
ORIGIN=re.compile(r"https://[a-z0-9][a-z0-9-]{0,62}\.crm[0-9]*\.dynamics\.com\Z")
ALIAS=re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
CATALOG="OWG_DATAVERSE_CATALOG"
PATH="/api/data/v9.2/"
FIELDS="auditid,_objectid_value,objecttypecode,createdon,operation,action,_userid_value"
# Audit operation codes, not inferred business outcomes.
OPS={1:"create",2:"update",3:"delete",5:"upsert",115:"archive",116:"retain",117:"rollback_retain",118:"restore"}


def guid(value):
    if not isinstance(value,str) or not GUID.fullmatch(value.lower()) or value.lower()=="00000000-0000-0000-0000-000000000000":
        raise rest.FeedError("invalid_dataverse_guid",502)
    return value.lower()


class Source(rest.Strict):
    origin:str=Field(max_length=180)
    tenant_id:str
    client_id:str
    organization_id:str
    application_user_id:str
    credential_env:str=Field(pattern=r"^OWG_DATAVERSE_SECRET_[A-Z0-9_]{1,48}$")

    @model_validator(mode="after")
    def valid(self):
        if not ORIGIN.fullmatch(self.origin):
            raise ValueError("fixed commercial Dataverse environment origin required")
        try:
            for k in ("tenant_id","client_id","organization_id","application_user_id"):
                setattr(self,k,guid(getattr(self,k)))
        except rest.FeedError:
            raise ValueError("invalid deployment GUID") from None
        return self


class Selection(rest.Strict):
    table:Table
    record_ids:list[str]=Field(default_factory=list,max_length=100)

    @model_validator(mode="after")
    def valid(self):
        try:
            self.record_ids=sorted(guid(v) for v in self.record_ids)
        except rest.FeedError:
            raise ValueError("invalid record GUID") from None
        if len(set(self.record_ids))!=len(self.record_ids):
            raise ValueError("duplicate record GUID")
        return self


class Create(rest.Strict):
    name:str=Field(min_length=1,max_length=100)
    source_alias:str=Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    source_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    selections:list[Selection]=Field(min_length=1,max_length=10)
    operations:list[Literal[1,2,3,5,115,116,117,118]]=Field(default_factory=lambda:[1,2,3],min_length=1,max_length=8)
    initial_since:str=Field(min_length=20,max_length=40)
    window_hours:StrictInt=Field(ge=1,le=168,default=24)
    overlap_minutes:StrictInt=Field(ge=0,le=1440,default=60)
    ingestion_lag_minutes:StrictInt=Field(ge=0,le=1440,default=15)
    page_size:StrictInt=Field(ge=1,le=500,default=200)
    confirm_configuration:StrictBool
    confirm_audit_retention:StrictBool
    confirm_application_identity:StrictBool
    confirm_unrestricted_records:StrictBool=False

    @model_validator(mode="after")
    def valid(self):
        if len({s.table for s in self.selections})!=len(self.selections) or len(set(self.operations))!=len(self.operations) or self.overlap_minutes>=self.window_hours*60:
            raise ValueError("duplicate scope or invalid overlap")
        if any(type(x) is not int for x in self.operations):
            raise ValueError("operation codes must be integers")
        return self


def catalog():
    try:
        raw=os.environ.get(CATALOG,"{}")
        if len(raw)>100000:
            raise ValueError()
        data=rest._strict_json(raw.encode())
        if not isinstance(data,dict) or len(data)>30 or any(not ALIAS.fullmatch(a) for a in data):
            raise ValueError()
        return {a:Source.model_validate(v).model_dump() for a,v in data.items()}
    except (ValueError,TypeError):
        raise rest.FeedError("invalid_dataverse_deployment_catalog",422) from None


def secret(src):
    value=rest.resolve_secret(src["credential_env"])
    if not 8<=len(value)<=4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise rest.FeedError("dataverse_secret_unavailable")
    return value


def available(src):
    try:
        secret(src);return True
    except rest.FeedError:
        return False


def catalog_public():
    return {"sources":[{"source_alias":a,"source_sha256":rest._digest(v),"origin":v["origin"],
                        "tenant_id":v["tenant_id"],"client_id":v["client_id"],"organization_id":v["organization_id"],
                        "application_user_id":v["application_user_id"],"credential_available":available(v)}
                       for a,v in sorted(catalog().items())],"provider_reads_performed":False}


def source(cfg):
    value=catalog().get(cfg["source_alias"])
    if value is None or rest._digest(value)!=cfg["source_sha256"]:
        raise rest.FeedError("dataverse_source_definition_changed")
    return value


def check_binding(p):
    src=source(p["config"]);value=secret(src)
    if not hmac.compare_digest(rest._key(p,"credential",value),p["credential_binding"]):
        raise rest.FeedError("credential_changed_reconfigure_required")
    return value


def create(value):
    try:
        spec=Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_dataverse_audit_profile",422) from None
    if not spec.confirm_configuration or not spec.confirm_audit_retention or not spec.confirm_application_identity:
        raise rest.FeedError("explicit_dataverse_configuration_identity_retention_required",403)
    if any(not s.record_ids for s in spec.selections) and not spec.confirm_unrestricted_records:
        raise rest.FeedError("explicit_dataverse_unrestricted_records_required",403)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in spec.name):
        raise rest.FeedError("invalid_profile_name",422)
    now=rest._now();since=rest._stamp(spec.initial_since)
    if not now-timedelta(days=365)<=since<now-timedelta(minutes=spec.ingestion_lag_minutes):
        raise rest.FeedError("dataverse_initial_since_outside_reviewed_window",422)
    cfg=spec.model_dump(exclude={"confirm_configuration","confirm_audit_retention","confirm_application_identity","confirm_unrestricted_records"})
    cfg.update(name=spec.name.strip(),initial_since=rest._iso(since),operations=sorted(spec.operations),
               selections=sorted([s.model_dump() for s in spec.selections],key=lambda s:s["table"]))
    return rest.create_profile(cfg,secret(source(cfg)),now,provider="dataverse")


def cutoff(p,now,start):
    result=now-timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    if result<=start:
        raise rest.FeedError("no_new_checkpoint_window")
    return result


def query(cfg,selection,since,until):
    table=selection["table"]
    if table not in TABLES:
        raise rest.FeedError("invalid_dataverse_table")
    ops=cfg["operations"]
    if not ops or any(type(v) is not int or v not in OPS for v in ops):
        raise rest.FeedError("invalid_dataverse_operation")
    filters=["createdon ge "+rest._iso(since),"createdon lt "+rest._iso(until),
             "objecttypecode eq '"+table+"'","("+" or ".join("operation eq "+str(v) for v in ops)+")"]
    if selection["record_ids"]:
        filters.append("("+" or ".join("_objectid_value eq "+guid(v) for v in selection["record_ids"])+")")
    return {"$select":FIELDS,"$filter":" and ".join(filters),"$orderby":"createdon asc,auditid asc"}


def project(p,selection,item,since,until):
    if not isinstance(item,dict) or item.get("objecttypecode")!=selection["table"]:
        raise rest.FeedError("dataverse_table_scope_mismatch",502)
    event=guid(item.get("auditid"));case=guid(item.get("_objectid_value"))
    if selection["record_ids"] and case not in selection["record_ids"]:
        raise rest.FeedError("dataverse_record_scope_mismatch",502)
    op=item.get("operation");action=item.get("action")
    if type(op) is not int or op not in p["config"]["operations"] or op not in OPS or type(action) is not int or not 0<=action<=100000:
        raise rest.FeedError("dataverse_operation_scope_mismatch",502)
    at=rest._stamp(item.get("createdon"))
    if not since<=at<until:
        raise rest.FeedError("dataverse_audit_outside_requested_window",502)
    actor=item.get("_userid_value")
    resource=rest._key(p,"dataverse_actor",guid(actor)) if actor is not None else ""
    return [rest._key(p,"dataverse_event",event),rest._key(p,"dataverse_case",selection["table"]+":"+case),
            "dataverse."+selection["table"]+"."+OPS[op]+".action_"+str(action),rest._iso(at),resource]


class Reader:
    def __init__(self,p,client):
        self.p=p;self.src=source(p["config"]);self.client=client
        self.root=self.src["origin"]+PATH;self.deadline=time.monotonic()+rest.MAX_SECONDS
        self.pages=0;self.bytes=0;self.access=None;self.token_until=0

    def request(self,method,url,*,params=None,form=None,headers=None):
        if self.pages>=rest.MAX_PAGES:
            raise rest.FeedError("provider_page_budget_exceeded")
        self.pages+=1
        rest._check(self.p)
        left=self.deadline-time.monotonic()
        if left<=0:
            raise rest.FeedError("read_time_budget_exceeded")
        try:
            with self.client.stream(method,url,params=params,data=form,headers=headers,timeout=min(10,left)) as r:
                if r.status_code==429:
                    try:
                        delay=max(60,min(86400,int(r.headers.get("Retry-After","60"))))
                    except ValueError:
                        delay=60
                    raise rest.FeedError("provider_rate_limited",429,delay)
                if r.status_code in (400,401,403) and method=="POST":
                    raise rest.FeedError("dataverse_application_auth_failed",502)
                if r.status_code in (401,403):
                    raise rest.FeedError("source_access_denied",502)
                if r.status_code!=200:
                    raise rest.FeedError("provider_http_failure",502)
                raw=bytearray()
                for chunk in r.iter_bytes(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw)>rest.MAX_PAGE_BYTES or self.bytes+len(raw)>rest.MAX_TOTAL_BYTES or time.monotonic()>self.deadline:
                        raise rest.FeedError("provider_response_budget_exceeded")
                self.bytes+=len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure",502) from None
        body=rest._strict_json(bytes(raw))
        if not isinstance(body,dict):
            raise rest.FeedError("invalid_dataverse_response",502)
        return body

    def authenticate(self):
        if self.access and self.token_until>time.monotonic()+10:
            return
        value=rest._check(self.p)
        body=self.request("POST","https://login.microsoftonline.com/"+self.src["tenant_id"]+"/oauth2/v2.0/token",
                          form={"grant_type":"client_credentials","client_id":self.src["client_id"],
                                "client_secret":value,"scope":self.src["origin"]+"/.default"},
                          headers={"Accept":"application/json"})
        token=body.get("access_token");expires=body.get("expires_in")
        if not isinstance(token,str) or not 16<=len(token)<=16384 or any(ord(c)<33 or ord(c)>126 for c in token) or str(body.get("token_type","")).lower()!="bearer" or type(expires) is not int or not 30<=expires<=86400:
            raise rest.FeedError("invalid_dataverse_token_response",502)
        self.access=token;self.token_until=time.monotonic()+expires

    def get(self,path,params=None):
        self.authenticate()
        return self.request("GET",self.root+path,params=params,headers={
            "Authorization":"Bearer "+self.access,"Accept":"application/json",
            "OData-MaxVersion":"4.0","OData-Version":"4.0",
            "Prefer":"odata.maxpagesize="+str(self.p["config"]["page_size"])})

    def identity(self):
        value=self.get("WhoAmI")
        if guid(value.get("OrganizationId"))!=self.src["organization_id"] or guid(value.get("UserId"))!=self.src["application_user_id"]:
            raise rest.FeedError("dataverse_application_identity_mismatch",502)

    def stream(self,selection,since,until,budget):
        original=query(self.p["config"],selection,since,until)
        params=original;seen=set();rows=[];cursor_seen=set();previous=None
        while True:
            body=self.get("audits",params)
            records=body.get("value")
            if not isinstance(records,list) or len(records)>self.p["config"]["page_size"]:
                raise rest.FeedError("invalid_dataverse_audit_page",502)
            for record in records:
                row=project(self.p,selection,record,since,until)
                at=rest._stamp(row[3])
                # Dataverse GUID tie sort order can differ from lexical order.
                # Verify timestamp monotonicity and exact distinct identities.
                if row[0] in seen or previous is not None and at<previous:
                    raise rest.FeedError("dataverse_duplicate_or_unordered_page",502)
                seen.add(row[0]);rows.append(row);previous=at
                if len(rows)>budget:
                    raise rest.FeedError("provider_row_budget_exceeded")
            cursor=body.get("@odata.nextLink")
            if cursor is None:
                return rows
            if not records or not isinstance(cursor,str) or len(cursor)>32000:
                raise rest.FeedError("invalid_dataverse_cursor",502)
            parts=urlsplit(cursor);base=urlsplit(self.root+"audits")
            if (parts.scheme,parts.netloc,parts.path)!=(base.scheme,base.netloc,base.path) or parts.fragment or parts.username or parts.password:
                raise rest.FeedError("invalid_dataverse_cursor",502)
            try:
                pairs=parse_qsl(parts.query,keep_blank_values=True,max_num_fields=12)
            except ValueError:
                raise rest.FeedError("invalid_dataverse_cursor",502) from None
            values=dict(pairs)
            if len(values)!=len(pairs) or set(values)-set(original)-{"$skiptoken"} or any(values.get(k)!=v for k,v in original.items() if k in values):
                raise rest.FeedError("dataverse_cursor_scope_changed",502)
            skip=values.get("$skiptoken")
            if not skip or len(skip)>24000 or any(ord(c)<32 or ord(c)==127 for c in skip) or skip in cursor_seen:
                raise rest.FeedError("invalid_dataverse_cursor",502)
            cursor_seen.add(skip);params={**original,"$skiptoken":skip}


def extract(p,since,until,client_factory=httpx.Client):
    rows=[];counts={};seen=set()
    with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
        reader=Reader(p,client);reader.identity()
        for selection in p["config"]["selections"]:
            batch=reader.stream(selection,since,until,rest.MAX_ROWS-len(rows))
            if any(r[0] in seen for r in batch):
                raise rest.FeedError("dataverse_cross_table_audit_identity_conflict",502)
            seen.update(r[0] for r in batch);rows.extend(batch);counts[selection["table"]]=len(batch)
        reader.identity()
        return rows,{"pages":reader.pages,"records_received":len(rows),"duplicate_records_in_window":0,
                     "table_counts":counts,"application_identity_readback_verified":True,
                     "field_values_retained":False,"provider_completeness_verified":False}
