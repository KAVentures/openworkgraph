"""Selected native HubSpot CRM property history evidence.

No object-wide discovery, arbitrary property query, provider writes, or
retained property values. Batch reads return history snapshots, not CDC.
"""
from datetime import timedelta
import hmac
import os
import re
import time
from typing import Literal

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator
from . import enterprise_rest_feed as rest

OBJECTS=("contacts","companies","deals","tickets")
Object=Literal["contacts","companies","deals","tickets"]
SCOPES={"contacts":"crm.objects.contacts.read","companies":"crm.objects.companies.read",
        "deals":"crm.objects.deals.read","tickets":"tickets"}
# Legacy tickets scope may grant writes; require explicit acknowledgement.
ALLOWED_SCOPES={"oauth",*SCOPES.values(),"crm.objects.tickets.read"}
CATALOG="OWG_HUBSPOT_CATALOG"
ORIGIN="https://api.hubapi.com"
IDENTITY="/oauth/v2/private-apps/get/access-token-info"
ID=re.compile(r"[1-9][0-9]{0,19}\Z")
PROPERTY=re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
ALIAS=re.compile(r"[a-z][a-z0-9_]{0,39}\Z")


def record_id(value):
    if not isinstance(value,str) or not ID.fullmatch(value):
        raise rest.FeedError("invalid_hubspot_record_id",422)
    return value


class Source(rest.Strict):
    hub_id:StrictInt=Field(ge=1,le=2**53-1)
    app_id:StrictInt=Field(ge=1,le=2**53-1)
    credential_env:str=Field(pattern=r"^OWG_HUBSPOT_TOKEN_[A-Z0-9_]{1,48}$")
    scopes:list[str]=Field(min_length=1,max_length=6)

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.scopes))!=len(self.scopes) or not set(self.scopes)<=ALLOWED_SCOPES:
            raise ValueError("exact supported credential scopes required")
        self.scopes=sorted(self.scopes)
        return self


class Selection(rest.Strict):
    object_type:Object
    record_ids:list[str]=Field(min_length=1,max_length=100)
    properties:list[str]=Field(min_length=1,max_length=20)

    @model_validator(mode="after")
    def valid(self):
        if any(not ID.fullmatch(x) for x in self.record_ids) or len(set(self.record_ids))!=len(self.record_ids):
            raise ValueError("exact distinct record IDs required")
        if any(not PROPERTY.fullmatch(x) for x in self.properties) or len(set(self.properties))!=len(self.properties):
            raise ValueError("exact distinct property names required")
        self.record_ids=sorted(self.record_ids);self.properties=sorted(self.properties)
        return self


class Create(rest.Strict):
    name:str=Field(min_length=1,max_length=100)
    source_alias:str=Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    source_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    selections:list[Selection]=Field(min_length=1,max_length=4)
    initial_since:str=Field(min_length=20,max_length=40)
    window_hours:StrictInt=Field(ge=1,le=168,default=24)
    overlap_minutes:StrictInt=Field(ge=0,le=1440,default=60)
    ingestion_lag_minutes:StrictInt=Field(ge=0,le=1440,default=15)
    batch_size:StrictInt=Field(ge=1,le=100,default=50)
    include_created:StrictBool=True
    confirm_configuration:StrictBool
    confirm_transient_history_values:StrictBool
    confirm_metadata_retention:StrictBool
    confirm_ticket_scope:StrictBool=False

    @model_validator(mode="after")
    def valid(self):
        if self.overlap_minutes>=self.window_hours*60 or len({x.object_type for x in self.selections})!=len(self.selections):
            raise ValueError("invalid overlap or duplicate object type")
        if 2+sum((len(x.record_ids)+self.batch_size-1)//self.batch_size for x in self.selections)>rest.MAX_PAGES:
            raise ValueError("scope exceeds request budget")
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
        raise rest.FeedError("invalid_hubspot_deployment_catalog",422) from None


def token(src):
    value=rest.resolve_secret(src["credential_env"])
    if not value.startswith("pat-") or not 16<=len(value)<=4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise rest.FeedError("hubspot_private_token_unavailable")
    return value


def available(src):
    try:
        token(src);return True
    except rest.FeedError:
        return False


def catalog_public():
    return {"sources":[{"source_alias":a,"source_sha256":rest._digest(v),"hub_id":v["hub_id"],
                        "app_id":v["app_id"],"scopes":v["scopes"],"credential_available":available(v)}
                       for a,v in sorted(catalog().items())],"provider_reads_performed":False}


def source(cfg):
    value=catalog().get(cfg["source_alias"])
    if value is None or rest._digest(value)!=cfg["source_sha256"]:
        raise rest.FeedError("hubspot_source_definition_changed")
    for s in cfg["selections"]:
        obj=s["object_type"]
        if SCOPES[obj] not in value["scopes"] and not (obj=="tickets" and "crm.objects.tickets.read" in value["scopes"]):
            raise rest.FeedError("hubspot_required_scope_missing")
    return value


def check_binding(p):
    cfg=p["config"];src=source(cfg);secret=token(src)
    if not hmac.compare_digest(rest._key(p,"credential",secret),p["credential_binding"]):
        raise rest.FeedError("credential_changed_reconfigure_required")
    return secret


def create(value):
    try:
        spec=Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_hubspot_history_profile",422) from None
    if not spec.confirm_configuration or not spec.confirm_transient_history_values or not spec.confirm_metadata_retention:
        raise rest.FeedError("explicit_hubspot_history_and_retention_required",403)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in spec.name):
        raise rest.FeedError("invalid_profile_name",422)
    now=rest._now();since=rest._stamp(spec.initial_since)
    if not now-timedelta(days=365)<=since<now-timedelta(minutes=spec.ingestion_lag_minutes):
        raise rest.FeedError("hubspot_initial_since_outside_reviewed_window",422)
    cfg=spec.model_dump(exclude={"confirm_configuration","confirm_transient_history_values","confirm_metadata_retention","confirm_ticket_scope"})
    cfg.update(name=spec.name.strip(),initial_since=rest._iso(since),
               selections=sorted([s.model_dump() for s in spec.selections],key=lambda s:s["object_type"]))
    src=source(cfg)
    # A legacy tickets grant is not asserted to be read-only even when
    # OWG's adapter issues only the two fixed read POST endpoints.
    if "tickets" in src["scopes"] and not spec.confirm_ticket_scope:
        raise rest.FeedError("explicit_hubspot_legacy_ticket_scope_required",403)
    return rest.create_profile(cfg,token(src),now,provider="hubspot")


def cutoff(p,now,start):
    result=now-timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    if result<=start:
        raise rest.FeedError("no_new_checkpoint_window")
    return result


def projection(p,selection,item,since,until):
    if not isinstance(item,dict) or item.get("archived") is not False:
        raise rest.FeedError("hubspot_record_archived_or_invalid",502)
    obj=selection["object_type"];record=record_id(item.get("id"))
    if record not in selection["record_ids"]:
        raise rest.FeedError("hubspot_record_scope_mismatch",502)
    created=rest._stamp(item.get("createdAt"))
    updated=rest._stamp(item.get("updatedAt"))
    if updated<created:
        raise rest.FeedError("hubspot_record_timestamp_inconsistent",502)
    histories=item.get("propertiesWithHistory")
    if not isinstance(histories,dict) or set(histories)-set(selection["properties"]):
        raise rest.FeedError("hubspot_history_scope_mismatch",502)
    # HubSpot may omit never-populated requested properties. Record the
    # omission explicitly, never claim complete history for that property.
    missing=len(set(selection["properties"])-set(histories))
    case=rest._key(p,"hubspot_case",obj+":"+record)
    rows=[];seen=set();versions=0;outside=0
    if p["config"]["include_created"] and since<=created<until:
        rows.append([rest._key(p,"hubspot_event",obj+":"+record+":created"),case,
                     "hubspot."+obj+".record_created",rest._iso(created),""])
    for prop,history in sorted(histories.items()):
        if not isinstance(history,list) or len(history)>1000:
            raise rest.FeedError("hubspot_invalid_or_oversized_history",502)
        for version in history:
            versions+=1
            if not isinstance(version,dict) or not isinstance(version.get("value"),str):
                raise rest.FeedError("hubspot_invalid_history_version",502)
            at=rest._stamp(version.get("timestamp"))
            # The API does not expose a stable version ID. Identity is
            # metadata-only (record/property/time); collisions fail closed.
            # Do not hash the value, sourceId, sourceLabel or user ID.
            identity=(prop,rest._iso(at))
            if identity in seen:
                raise rest.FeedError("hubspot_ambiguous_same_timestamp_versions",502)
            seen.add(identity)
            if not since<=at<until:
                outside+=1;continue
            event=rest._key(p,"hubspot_event",obj+":"+record+":"+prop+":"+identity[1])
            rows.append([event,case,"hubspot."+obj+".property."+prop,identity[1],""])
    return rows,{"versions":versions,"outside_window":outside,"missing_properties":missing}


class Reader:
    def __init__(self,p,client):
        self.p=p;self.src=source(p["config"]);self.client=client
        self.deadline=time.monotonic()+rest.MAX_SECONDS;self.pages=0;self.bytes=0

    def post(self,path,payload):
        if self.pages>=rest.MAX_PAGES:
            raise rest.FeedError("provider_page_budget_exceeded")
        self.pages+=1
        secret=rest._check(self.p)
        body=payload if path!=IDENTITY else {"tokenKey":secret}
        remaining=self.deadline-time.monotonic()
        if remaining<=0:
            raise rest.FeedError("read_time_budget_exceeded")
        try:
            with self.client.stream("POST",ORIGIN+path,json=body,
                    headers={"Authorization":"Bearer "+secret,"Accept":"application/json"},
                    timeout=min(10,remaining)) as response:
                if response.status_code in (401,403):
                    raise rest.FeedError("source_access_denied",502)
                if response.status_code==429:
                    try:
                        delay=max(60,min(86400,int(response.headers.get("Retry-After","60"))))
                    except ValueError:
                        delay=60
                    raise rest.FeedError("provider_rate_limited",429,delay)
                # A partial (207), pending (202) or asynchronous response
                # is never accepted as a completed exact batch.
                if response.status_code!=200:
                    raise rest.FeedError("provider_http_failure",502)
                raw=bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw)>rest.MAX_PAGE_BYTES or self.bytes+len(raw)>rest.MAX_TOTAL_BYTES or time.monotonic()>self.deadline:
                        raise rest.FeedError("provider_response_budget_exceeded")
                self.bytes+=len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure",502) from None
        result=rest._strict_json(bytes(raw))
        if not isinstance(result,dict):
            raise rest.FeedError("invalid_hubspot_response",502)
        return result

    def identity(self):
        body=self.post(IDENTITY,{})
        scopes=body.get("scopes")
        if (type(body.get("hubId")) is not int or type(body.get("appId")) is not int
            or body["hubId"]!=self.src["hub_id"] or body["appId"]!=self.src["app_id"]
            or not isinstance(scopes,list) or any(not isinstance(s,str) for s in scopes)
            or len(scopes)!=len(set(scopes)) or set(scopes)!=set(self.src["scopes"])):
            raise rest.FeedError("hubspot_account_app_or_scope_mismatch",502)

    def batch(self,selection,ids,since,until):
        payload={"inputs":[{"id":r} for r in ids],"properties":["hs_object_id"],
                 "propertiesWithHistory":selection["properties"]}
        result=self.post("/crm/v3/objects/"+selection["object_type"]+"/batch/read",payload)
        records=result.get("results")
        if (result.get("status")!="COMPLETE" or result.get("errors") not in (None,[])
            or result.get("numErrors",0)!=0 or result.get("paging") is not None
            or not isinstance(records,list) or len(records)!=len(ids)):
            raise rest.FeedError("hubspot_incomplete_exact_batch",502)
        actual=[]
        for item in records:
            if not isinstance(item,dict):
                raise rest.FeedError("hubspot_incomplete_exact_batch",502)
            actual.append(record_id(item.get("id")))
        if len(set(actual))!=len(actual) or set(actual)!=set(ids):
            raise rest.FeedError("hubspot_batch_record_set_mismatch",502)
        return [projection(self.p,selection,item,since,until) for item in records]


def extract(p,since,until,client_factory=httpx.Client):
    rows=[];counts={};seen=set();versions=records=missing=outside=0
    with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
        reader=Reader(p,client);reader.identity()
        size=p["config"]["batch_size"]
        for selection in p["config"]["selections"]:
            ids=selection["record_ids"];obj=selection["object_type"]
            receipt={"records":0,"versions":0,"outside_window":0,"missing_properties":0,"events":0}
            for offset in range(0,len(ids),size):
                for batch,metrics in reader.batch(selection,ids[offset:offset+size],since,until):
                    records+=1;versions+=metrics["versions"];missing+=metrics["missing_properties"];outside+=metrics["outside_window"]
                    if records+versions>rest.MAX_ROWS or len(rows)+len(batch)>rest.MAX_ROWS:
                        raise rest.FeedError("provider_row_budget_exceeded")
                    if any(r[0] in seen for r in batch) or len({r[0] for r in batch})!=len(batch):
                        raise rest.FeedError("hubspot_duplicate_event_identity",502)
                    seen.update(r[0] for r in batch);rows.extend(batch)
                    receipt["records"]+=1;receipt["events"]+=len(batch)
                    for k,v in metrics.items():receipt[k]+=v
            counts[obj]=receipt
        reader.identity()
        return rows,{"pages":reader.pages,"records_received":records+versions,
                     "duplicate_records_in_window":0,"object_counts":counts,
                     "history_versions_observed":versions,"versions_outside_window":outside,
                     "missing_requested_properties":missing,"account_app_scope_readback_verified":True,
                     "native_history_version_ids_available":False,"field_values_retained":False,
                     "provider_completeness_verified":False}
