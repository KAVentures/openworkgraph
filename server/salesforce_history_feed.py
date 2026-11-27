"""Native Salesforce field-history ingestion through the common enterprise feed runtime.

No arbitrary SOQL, old/new field values, record contents or provider writes.
The configured instance, organization, principal and field allowlist are
explicitly reviewed; fixed history projections are committed as one window.
"""
from datetime import timedelta
import hmac
import re
import time
from typing import Literal

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import connector_oauth as oauth
from . import enterprise_rest_feed as rest

API="/services/data/v60.0/query"
OBJECTS={
    "AccountHistory":("AccountId","001"),
    "ContactHistory":("ContactId","003"),
    "LeadHistory":("LeadId","00Q"),
    "OpportunityFieldHistory":("OpportunityId","006"),
    "CaseHistory":("CaseId","500"),
}
ID=re.compile(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?\Z")
FIELD=re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\Z")
CONNECTION=re.compile(r"conn_[a-f0-9]{16}\Z")
HOST=re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,250}[a-z0-9])?\Z")
CURSOR=re.compile(r"/services/data/v60\.0/query/[A-Za-z0-9_-]{1,200}\Z")


def sfid(value,prefix=None):
    """Canonical case-sensitive 15-char identity; validate 18-char checksum."""
    if not isinstance(value,str) or not ID.fullmatch(value) or prefix and not value.startswith(prefix):
        raise rest.FeedError("invalid_salesforce_identity",502)
    if len(value)==18:
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
        checksum="".join(alphabet[sum(1<<j for j,c in enumerate(value[i:i+5]) if c.isupper())] for i in (0,5,10))
        if value[15:]!=checksum:
            raise rest.FeedError("invalid_salesforce_identity_checksum",502)
    return value[:15]


def host(value):
    if (not isinstance(value,str) or not HOST.fullmatch(value) or ".." in value or value.count(".")<2
            or not value.endswith((".salesforce.com",".force.com"))):
        raise rest.FeedError("invalid_salesforce_instance",422)
    return value


class Selection(rest.Strict):
    object: Literal["AccountHistory","ContactHistory","LeadHistory","OpportunityFieldHistory","CaseHistory"]
    fields: list[str]=Field(min_length=1,max_length=30)
    record_ids: list[str]=Field(default_factory=list,max_length=100)

    @model_validator(mode="after")
    def valid(self):
        if any(not FIELD.fullmatch(f) for f in self.fields) or len(set(self.fields))!=len(self.fields):
            raise ValueError("invalid or duplicate field")
        try:
            ids=[sfid(v,OBJECTS[self.object][1]) for v in self.record_ids]
        except rest.FeedError:
            raise ValueError("invalid record ID") from None
        if len(set(ids))!=len(ids):
            raise ValueError("duplicate record identity")
        return self


class Create(rest.Strict):
    name: str=Field(min_length=1,max_length=100)
    connection_id: str=Field(pattern=r"^conn_[a-f0-9]{16}$")
    instance_host: str=Field(min_length=5,max_length=253)
    organization_id: str=Field(min_length=15,max_length=18)
    user_id: str=Field(min_length=15,max_length=18)
    selections: list[Selection]=Field(min_length=1,max_length=5)
    initial_since: str=Field(min_length=20,max_length=40)
    window_hours: StrictInt=Field(ge=1,le=168,default=24)
    overlap_minutes: StrictInt=Field(ge=0,le=1440,default=60)
    ingestion_lag_minutes: StrictInt=Field(ge=0,le=1440,default=15)
    page_size: StrictInt=Field(ge=200,le=2000,default=200)
    confirm_configuration: StrictBool
    confirm_field_labels: StrictBool
    confirm_unrestricted_records: StrictBool=False
    confirm_identity_metadata: StrictBool

    @model_validator(mode="after")
    def valid(self):
        if self.overlap_minutes>=self.window_hours*60 or len({s.object for s in self.selections})!=len(self.selections):
            raise ValueError("invalid window or duplicate history object")
        return self


def grant(connection_id,expected_host):
    try:
        conn=next((x for x in oauth.status()["connections"] if x.get("connection_id")==connection_id),None)
        actual=oauth.api_host(connection_id)
    except Exception:
        raise rest.FeedError("salesforce_grant_unavailable") from None
    if not conn or conn.get("provider")!="salesforce" or actual!=expected_host:
        raise rest.FeedError("salesforce_grant_unavailable")
    scopes=set(conn.get("scopes",[]))
    if "api" not in scopes or not scopes.intersection({"id","openid","profile"}) or not scopes<= {"api","refresh_token","offline_access","id","openid","profile","email","address","phone"}:
        raise rest.FeedError("salesforce_grant_scope_mismatch")
    if conn.get("reauthorization_required") or conn.get("has_refresh_token") is not True:
        raise rest.FeedError("salesforce_reconnect_required")
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
        raise rest.FeedError("invalid_salesforce_history_profile",422) from None
    if not spec.confirm_configuration or not spec.confirm_field_labels or not spec.confirm_identity_metadata:
        raise rest.FeedError("explicit_salesforce_configuration_and_retention_required",403)
    if any(not s.record_ids for s in spec.selections) and not spec.confirm_unrestricted_records:
        raise rest.FeedError("explicit_salesforce_unrestricted_records_required",403)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 for c in spec.name):
        raise rest.FeedError("invalid_profile_name",422)
    origin=host(spec.instance_host)
    org=sfid(spec.organization_id,"00D");user=sfid(spec.user_id,"005")
    identity=grant(spec.connection_id,origin)
    now=rest._now();since=rest._stamp(spec.initial_since)
    if since.microsecond or not now-timedelta(days=365)<=since<now-timedelta(minutes=spec.ingestion_lag_minutes):
        raise rest.FeedError("salesforce_initial_since_requires_whole_second_within_365_days",422)
    cfg=spec.model_dump(exclude={"confirm_configuration","confirm_field_labels","confirm_unrestricted_records","confirm_identity_metadata"})
    cfg.update(name=spec.name.strip(),organization_id=org,user_id=user,initial_since=rest._iso(since),
               selections=sorted([{"object":s.object,"fields":sorted(s.fields),
                                   "record_ids":sorted(sfid(v,OBJECTS[s.object][1]) for v in s.record_ids)} for s in spec.selections],key=lambda s:s["object"]))
    return rest.create_profile(cfg,identity,now,provider="salesforce")


def cutoff(p,now,start):
    result=(now-timedelta(minutes=p["config"]["ingestion_lag_minutes"])).replace(microsecond=0)
    if result<=start:
        raise rest.FeedError("no_new_checkpoint_window")
    return result


def query(selection,since,until):
    obj=selection["object"];parent,_=OBJECTS[obj]
    # All interpolated identifiers are validated against fixed schema or
    # strict alphanumeric allowlists. No arbitrary query string is accepted.
    fields=selection["fields"];ids=selection["record_ids"]
    if not fields or any(not FIELD.fullmatch(f) for f in fields):
        raise rest.FeedError("invalid_salesforce_field_filter")
    conditions=["CreatedDate >= "+rest._iso(since),"CreatedDate < "+rest._iso(until),
                "Field IN ("+",".join("'"+f+"'" for f in fields)+")"]
    if ids:
        conditions.append(parent+" IN ("+",".join("'"+sfid(v,OBJECTS[obj][1])+"'" for v in ids)+")")
    return "SELECT Id,"+parent+",CreatedById,CreatedDate,Field FROM "+obj+" WHERE "+" AND ".join(conditions)+" ORDER BY CreatedDate ASC,Id ASC"


def project(p,selection,item,since,until):
    obj=selection["object"];parent,prefix=OBJECTS[obj]
    if not isinstance(item,dict) or not isinstance(item.get("attributes"),dict) or item["attributes"].get("type")!=obj:
        raise rest.FeedError("salesforce_history_object_mismatch",502)
    event=sfid(item.get("Id"));case=sfid(item.get(parent),prefix);actor=sfid(item.get("CreatedById"),"005")
    field=item.get("Field")
    if field not in selection["fields"] or selection["record_ids"] and case not in selection["record_ids"]:
        raise rest.FeedError("salesforce_filter_mismatch",502)
    at=rest._stamp(item.get("CreatedDate"))
    if not since<=at<until:
        raise rest.FeedError("salesforce_event_outside_requested_window",502)
    return [rest._key(p,"salesforce_event",obj+":"+event),rest._key(p,"salesforce_case",obj+":"+case),
            "salesforce."+obj+"."+field,rest._iso(at),rest._key(p,"salesforce_actor",actor)]


class Reader:
    def __init__(self,p,client):
        self.p=p;self.client=client;self.origin="https://"+p["config"]["instance_host"]
        self.deadline=time.monotonic()+rest.MAX_SECONDS;self.bytes=0;self.pages=0

    def get(self,path,params=None):
        rest._check(self.p)
        try:
            token=oauth.bearer_for(self.p["config"]["connection_id"],"salesforce")
        except oauth.OAuthError:
            raise rest.FeedError("salesforce_reconnect_required") from None
        # A refresh can change the stored API instance. Recheck before
        # sending the bearer to the reviewed host.
        rest._check(self.p)
        remaining=self.deadline-time.monotonic()
        if remaining<=0:
            raise rest.FeedError("read_time_budget_exceeded")
        try:
            with self.client.stream("GET",self.origin+path,params=params,
                    headers={"Authorization":"Bearer "+str(token),"Accept":"application/json",
                             "Sforce-Query-Options":"batchSize="+str(self.p["config"]["page_size"])},
                    timeout=min(10,remaining)) as response:
                if response.status_code in (401,403):
                    # Salesforce may report REQUEST_LIMIT_EXCEEDED as 403.
                    # Never persist raw response/error bodies.
                    if response.status_code==403:
                        raw=bytearray()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            raw.extend(chunk)
                            if len(raw)>rest.MAX_PAGE_BYTES or self.bytes+len(raw)>rest.MAX_TOTAL_BYTES or time.monotonic()>self.deadline:
                                raise rest.FeedError("provider_response_budget_exceeded")
                        value=rest._strict_json(bytes(raw))
                        if isinstance(value,list) and any(isinstance(v,dict) and v.get("errorCode")=="REQUEST_LIMIT_EXCEEDED" for v in value):
                            raise rest.FeedError("provider_rate_limited",429,3600)
                    raise rest.FeedError("source_access_denied",502)
                if response.status_code==429:
                    try:
                        delay=max(60,min(86400,int(response.headers.get("Retry-After","60"))))
                    except ValueError:
                        delay=60
                    raise rest.FeedError("provider_rate_limited",429,delay)
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
        body=rest._strict_json(bytes(raw))
        if not isinstance(body,dict):
            raise rest.FeedError("invalid_salesforce_response",502)
        return body

    def identity(self):
        cfg=self.p["config"]
        # The existing sign-in must include identity scope. This documented
        # identity URL is assembled from reviewed org/user IDs on the same
        # instance, not taken from an untrusted provider response.
        data=self.get("/id/"+cfg["organization_id"]+"/"+cfg["user_id"])
        if sfid(data.get("organization_id"),"00D")!=cfg["organization_id"] or sfid(data.get("user_id"),"005")!=cfg["user_id"]:
            raise rest.FeedError("salesforce_principal_mismatch",502)

    def history(self,selection,since,until,budget):
        params={"q":query(selection,since,until)};path=API
        expected=None;rows={};seen=set();received=0
        while self.pages<rest.MAX_PAGES:
            self.pages+=1;body=self.get(path,params)
            records=body.get("records");total=body.get("totalSize");done=body.get("done")
            if type(total) is not int or total<0 or type(done) is not bool or not isinstance(records,list):
                raise rest.FeedError("invalid_salesforce_query_page",502)
            if expected is None:
                expected=total
            if total!=expected or expected>budget:
                raise rest.FeedError("salesforce_query_count_changed_or_budget_exceeded",502)
            received+=len(records)
            if received>expected or received>budget or len(records)>2000:
                raise rest.FeedError("salesforce_query_count_changed_or_budget_exceeded",502)
            for record in records:
                row=project(self.p,selection,record,since,until)
                if row[0] in rows:
                    raise rest.FeedError("duplicate_salesforce_query_identity",502)
                rows[row[0]]=row
            cursor=body.get("nextRecordsUrl")
            if done:
                if cursor is not None or received!=expected:
                    raise rest.FeedError("incomplete_salesforce_query",502)
                return list(rows.values())
            if not records or not isinstance(cursor,str) or not CURSOR.fullmatch(cursor) or cursor in seen:
                raise rest.FeedError("invalid_salesforce_query_cursor",502)
            seen.add(cursor);path=cursor;params=None
        raise rest.FeedError("provider_page_budget_exceeded")


def extract(p,since,until,client_factory=httpx.Client):
    rows=[];counts={}
    with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
        reader=Reader(p,client);reader.identity()
        for selection in p["config"]["selections"]:
            batch=reader.history(selection,since,until,rest.MAX_ROWS-len(rows))
            counts[selection["object"]]=len(batch);rows.extend(batch)
        reader.identity()
        return rows,{"pages":reader.pages,"records_received":len(rows),"duplicate_records_in_window":0,
                     "object_counts":counts,"principal_readback_verified":True,
                     "field_values_retained":False,"provider_completeness_verified":False}
