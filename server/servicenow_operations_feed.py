"""Native ServiceNow operational history and creation evidence.

Only fixed Table API GET projections, exact table/field/record filters,
UTC integration-account semantics and count-checked pagination. No old/new
values, descriptions, usernames, approval decisions or reference payloads.
"""
from datetime import datetime, timedelta, timezone
import hmac
import re
import time
from typing import Literal

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import connector_oauth as oauth
from . import enterprise_rest_feed as rest

TABLES=("incident","problem","change_request","change_task","sc_request","sc_req_item","sc_task","sysapproval_approver")
TABLE=Literal["incident","problem","change_request","change_task","sc_request","sc_req_item","sc_task","sysapproval_approver"]
ID=re.compile(r"[0-9a-f]{32}\Z")
FIELD=re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
HOST=re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,250}[a-z0-9])?\Z")
DATE=re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\Z")
AUDIT_FIELDS=("sys_id","documentkey","tablename","fieldname","sys_created_on")
CREATION_FIELDS=("sys_id","sys_created_on")


class Selection(rest.Strict):
    table: TABLE
    fields: list[str]=Field(default_factory=list,max_length=30)
    record_ids: list[str]=Field(default_factory=list,max_length=100)
    include_created: StrictBool=False

    @model_validator(mode="after")
    def valid(self):
        if not self.fields and not self.include_created:
            raise ValueError("choose at least one audit field or creation stream")
        if any(not FIELD.fullmatch(f) for f in self.fields) or len(set(self.fields))!=len(self.fields):
            raise ValueError("invalid or duplicate audit field")
        if any(not ID.fullmatch(i) for i in self.record_ids) or len(set(self.record_ids))!=len(self.record_ids):
            raise ValueError("invalid or duplicate record ID")
        return self


class Create(rest.Strict):
    name: str=Field(min_length=1,max_length=100)
    connection_id: str=Field(pattern=r"^conn_[a-f0-9]{16}$")
    instance_host: str=Field(min_length=5,max_length=253)
    selections: list[Selection]=Field(min_length=1,max_length=8)
    initial_since: str=Field(min_length=20,max_length=40)
    window_hours: StrictInt=Field(ge=1,le=168,default=24)
    overlap_minutes: StrictInt=Field(ge=0,le=1440,default=60)
    ingestion_lag_minutes: StrictInt=Field(ge=0,le=1440,default=15)
    page_size: StrictInt=Field(ge=1,le=500,default=200)
    verify_repeat: StrictBool=True
    confirm_configuration: StrictBool
    confirm_utc_account: StrictBool
    confirm_audit_retention: StrictBool
    confirm_creation_retention: StrictBool=False
    confirm_unrestricted_records: StrictBool=False

    @model_validator(mode="after")
    def valid(self):
        if self.overlap_minutes>=self.window_hours*60 or len({s.table for s in self.selections})!=len(self.selections):
            raise ValueError("invalid window or duplicate table")
        streams=sum(bool(s.fields)+s.include_created for s in self.selections)
        if streams*(2 if self.verify_repeat else 1)>rest.MAX_PAGES:
            raise ValueError("selected streams exceed minimum read budget")
        return self


def host(value):
    if (not isinstance(value,str) or not HOST.fullmatch(value) or ".." in value or
            not value.endswith((".service-now.com",".servicenowservices.com"))):
        raise rest.FeedError("invalid_servicenow_instance",422)
    return value


def grant(connection_id,expected_host):
    try:
        conn=next((x for x in oauth.status()["connections"] if x.get("connection_id")==connection_id),None)
        actual=oauth.api_host(connection_id)
    except Exception:
        raise rest.FeedError("servicenow_grant_unavailable") from None
    if not conn or conn.get("provider")!="servicenow" or actual!=expected_host or conn.get("host")!=expected_host:
        raise rest.FeedError("servicenow_grant_unavailable")
    if set(conn.get("scopes",[]))!={"useraccount"}:
        raise rest.FeedError("servicenow_grant_scope_mismatch")
    if conn.get("reauthorization_required") or conn.get("has_refresh_token") is not True:
        raise rest.FeedError("servicenow_reconnect_required")
    return "oauth_connection:"+connection_id+":"+expected_host


def check_binding(p):
    cfg=p["config"]
    identity=grant(cfg["connection_id"],host(cfg["instance_host"]))
    if not hmac.compare_digest(rest._key(p,"credential",identity),p["credential_binding"]):
        raise rest.FeedError("credential_changed_reconfigure_required")
    return identity


def create(value):
    try:
        spec=Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_servicenow_operations_profile",422) from None
    if not spec.confirm_configuration or not spec.confirm_utc_account:
        raise rest.FeedError("explicit_servicenow_configuration_and_utc_account_required",403)
    if any(s.fields for s in spec.selections) and not spec.confirm_audit_retention:
        raise rest.FeedError("explicit_servicenow_audit_retention_required",403)
    if any(s.include_created for s in spec.selections) and not spec.confirm_creation_retention:
        raise rest.FeedError("explicit_servicenow_creation_retention_required",403)
    if any(not s.record_ids for s in spec.selections) and not spec.confirm_unrestricted_records:
        raise rest.FeedError("explicit_servicenow_unrestricted_records_required",403)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in spec.name):
        raise rest.FeedError("invalid_profile_name",422)
    origin=host(spec.instance_host)
    identity=grant(spec.connection_id,origin)
    now=rest._now();since=rest._stamp(spec.initial_since)
    if since.microsecond or not now-timedelta(days=365)<=since<now-timedelta(minutes=spec.ingestion_lag_minutes):
        raise rest.FeedError("servicenow_initial_since_requires_whole_second_within_365_days",422)
    cfg=spec.model_dump(exclude={"confirm_configuration","confirm_utc_account","confirm_audit_retention",
                                 "confirm_creation_retention","confirm_unrestricted_records"})
    cfg.update(name=spec.name.strip(),initial_since=rest._iso(since),
               selections=sorted([{"table":s.table,"fields":sorted(s.fields),"record_ids":sorted(s.record_ids),
                                   "include_created":s.include_created} for s in spec.selections],key=lambda s:s["table"]))
    return rest.create_profile(cfg,identity,now,provider="servicenow")


def cutoff(p,now,start):
    result=(now-timedelta(minutes=p["config"]["ingestion_lag_minutes"])).replace(microsecond=0)
    if result<=start:
        raise rest.FeedError("no_new_checkpoint_window")
    return result


def timestamp(value):
    if not isinstance(value,str) or not DATE.fullmatch(value):
        raise rest.FeedError("invalid_servicenow_utc_timestamp",502)
    try:
        at=datetime.strptime(value,"%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if not 1900<=at.year<=2100:
            raise ValueError()
        return at
    except ValueError:
        raise rest.FeedError("invalid_servicenow_utc_timestamp",502) from None


def params(selection,mode,since,until,limit,offset):
    table=selection["table"];fields=selection["fields"];ids=selection["record_ids"]
    if table not in TABLES or mode not in ("audit","created") or any(not ID.fullmatch(v) for v in ids):
        raise rest.FeedError("invalid_servicenow_saved_scope")
    q=["sys_created_on>="+since.strftime("%Y-%m-%d %H:%M:%S"),
       "sys_created_on<"+until.strftime("%Y-%m-%d %H:%M:%S")]
    if mode=="audit":
        if not fields or any(not FIELD.fullmatch(v) for v in fields):
            raise rest.FeedError("invalid_servicenow_saved_scope")
        q+=["tablename="+table,"fieldnameIN"+",".join(fields)]
    if ids:
        q.append(("documentkey" if mode=="audit" else "sys_id")+"IN"+",".join(ids))
    q+=["ORDERBYsys_created_on","ORDERBYsys_id"]
    return {"sysparm_query":"^".join(q),"sysparm_fields":",".join(AUDIT_FIELDS if mode=="audit" else CREATION_FIELDS),
            "sysparm_display_value":"false","sysparm_exclude_reference_link":"true",
            "sysparm_no_count":"false","sysparm_limit":str(limit),"sysparm_offset":str(offset)}


def project(p,selection,mode,raw,since,until):
    keys=AUDIT_FIELDS if mode=="audit" else CREATION_FIELDS
    if not isinstance(raw,dict) or any(not isinstance(raw.get(k),str) for k in keys):
        raise rest.FeedError("invalid_servicenow_projected_fields",502)
    table=selection["table"]
    event=raw["sys_id"];case=raw["documentkey"] if mode=="audit" else event
    if not ID.fullmatch(event) or not ID.fullmatch(case):
        raise rest.FeedError("invalid_servicenow_source_identity",502)
    if selection["record_ids"] and case not in selection["record_ids"]:
        raise rest.FeedError("servicenow_record_filter_mismatch",502)
    if mode=="audit":
        if raw["tablename"]!=table or raw["fieldname"] not in selection["fields"]:
            raise rest.FeedError("servicenow_audit_filter_mismatch",502)
        activity="servicenow."+table+".field."+raw["fieldname"]
    else:
        activity="servicenow."+table+".record_created"
    at=timestamp(raw["sys_created_on"])
    if not since<=at<until:
        raise rest.FeedError("servicenow_event_outside_requested_window",502)
    return (at,event),[rest._key(p,"servicenow_event",mode+":"+table+":"+event),
                       rest._key(p,"servicenow_case",table+":"+case),activity,rest._iso(at),""]


class Reader:
    def __init__(self,p,client):
        self.p=p;self.client=client;self.origin="https://"+p["config"]["instance_host"]
        self.deadline=time.monotonic()+rest.MAX_SECONDS;self.bytes=0;self.pages=0

    def get(self,table,query):
        if self.pages>=rest.MAX_PAGES:
            raise rest.FeedError("provider_page_budget_exceeded")
        self.pages+=1
        rest._check(self.p)
        try:
            token=oauth.bearer_for(self.p["config"]["connection_id"],"servicenow")
        except oauth.OAuthError:
            raise rest.FeedError("servicenow_reconnect_required") from None
        rest._check(self.p)
        remaining=self.deadline-time.monotonic()
        if remaining<=0:
            raise rest.FeedError("read_time_budget_exceeded")
        try:
            with self.client.stream("GET",self.origin+"/api/now/table/"+table,params=query,
                    headers={"Authorization":"Bearer "+str(token),"Accept":"application/json"},
                    timeout=min(10,remaining)) as response:
                if response.status_code in (401,403):
                    raise rest.FeedError("source_access_denied",502)
                if response.status_code==429:
                    try:
                        delay=max(60,min(86400,int(response.headers.get("Retry-After","60"))))
                    except ValueError:
                        delay=60
                    raise rest.FeedError("provider_rate_limited",429,delay)
                if response.status_code!=200:
                    raise rest.FeedError("provider_http_failure",502)
                values=response.headers.get_list("X-Total-Count")
                if len(values)!=1 or not re.fullmatch(r"[0-9]{1,12}",values[0]):
                    raise rest.FeedError("servicenow_total_count_unavailable",502)
                total=int(values[0])
                raw=bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw)>rest.MAX_PAGE_BYTES or self.bytes+len(raw)>rest.MAX_TOTAL_BYTES or time.monotonic()>self.deadline:
                        raise rest.FeedError("provider_response_budget_exceeded")
                self.bytes+=len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure",502) from None
        body=rest._strict_json(bytes(raw))
        if not isinstance(body,dict) or set(body)!={"result"} or not isinstance(body["result"],list):
            raise rest.FeedError("invalid_servicenow_response",502)
        return total,body["result"]

    def stream(self,selection,mode,since,until,budget):
        page_size=self.p["config"]["page_size"]
        offset=0;expected=None;previous=None;seen=set();rows=[]
        table="sys_audit" if mode=="audit" else selection["table"]
        while True:
            total,records=self.get(table,params(selection,mode,since,until,page_size,offset))
            if expected is None:
                expected=total
            if total!=expected or expected>budget:
                raise rest.FeedError("servicenow_count_changed_or_budget_exceeded",502)
            # Table API applies limit before ACL evaluation. Refuse an
            # unexpectedly short page instead of assuming it is the end.
            if len(records)!=min(page_size,expected-offset):
                raise rest.FeedError("servicenow_page_count_mismatch_or_acl_gap",502)
            for record in records:
                order,row=project(self.p,selection,mode,record,since,until)
                if row[0] in seen or previous is not None and order<=previous:
                    raise rest.FeedError("servicenow_duplicate_or_unordered_page",502)
                seen.add(row[0]);rows.append(row);previous=order
            offset+=len(records)
            if offset==expected:
                return rows


def extract(p,since,until,client_factory=httpx.Client):
    rows=[];counts={}
    with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
        reader=Reader(p,client)
        for selection in p["config"]["selections"]:
            for mode in ("audit","created"):
                if not (selection["fields"] if mode=="audit" else selection["include_created"]):
                    continue
                budget=rest.MAX_ROWS-len(rows)
                batch=reader.stream(selection,mode,since,until,budget)
                if p["config"]["verify_repeat"]:
                    repeated=reader.stream(selection,mode,since,until,budget)
                    if repeated!=batch:
                        raise rest.FeedError("servicenow_repeat_read_mismatch",502)
                counts[selection["table"]+":"+mode]=len(batch)
                rows.extend(batch)
        return rows,{"pages":reader.pages,"records_received":len(rows),"duplicate_records_in_window":0,
                     "stream_counts":counts,"repeat_read_matched":p["config"]["verify_repeat"],
                     "instance_grant_bound":True,"provider_principal_independently_verified":False,
                     "provider_completeness_verified":False,"field_values_retained":False,
                     "resource_identity_retained":False}
