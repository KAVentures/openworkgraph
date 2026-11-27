"""Native Google Admin SDK Reports API ingestion.

Uses the common enterprise feed transaction, lease, exact source-key and
schedule lifecycle. No Google content API, directory enumeration, email,
IP address or sensitive audit parameter is requested for actor-only scopes.
Drive document grouping requests ordinary string parameters transiently;
only the exact doc_id is retained as a profile-local pseudonym.
"""
from collections import Counter
from datetime import timedelta
import hmac
import json
import re
import time
from typing import Literal

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import connector_oauth as oauth
from . import enterprise_rest_feed as rest

SCOPE = "https://www.googleapis.com/auth/admin.reports.audit.readonly"
ROOT = "https://admin.googleapis.com/admin/reports/v1/activity/users/"
APPLICATIONS = ("drive","calendar","admin","login","gmail","chat","meet")
FORMAT = "openworkgraph.google-workspace-audit.v1"
CUSTOMER = re.compile(r"[A-Za-z0-9_-]{2,64}\Z")
USER = re.compile(r"(?:all|[0-9]{5,64})\Z")
CONNECTION = re.compile(r"conn_[a-f0-9]{16}\Z")
LABEL = re.compile(r"[A-Za-z0-9_.-]{1,70}\Z")
QUALIFIER = re.compile(r"-?[0-9]{1,20}\Z")
PLACEHOLDER = "105250506097979753968"


class Create(rest.Strict):
    name: str = Field(min_length=1,max_length=100)
    connection_id: str = Field(pattern=r"^conn_[a-f0-9]{16}$")
    customer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{2,64}$")
    user_key: str = Field(pattern=r"^(all|[0-9]{5,64})$")
    application: Literal["drive","calendar","admin","login","gmail","chat","meet"]
    case_basis: Literal["actor","audit_record","drive_document"]
    initial_since: str = Field(min_length=20,max_length=40)
    window_hours: StrictInt = Field(ge=1,le=168,default=24)
    overlap_minutes: StrictInt = Field(ge=0,le=1440,default=60)
    ingestion_lag_minutes: StrictInt = Field(ge=0,le=1440,default=60)
    page_size: StrictInt = Field(ge=1,le=500,default=200)
    confirm_configuration: StrictBool
    confirm_audit_retention: StrictBool
    confirm_domain_scope: StrictBool
    confirm_drive_parameters: StrictBool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.overlap_minutes >= self.window_hours*60:
            raise ValueError("overlap must be shorter than advancing window")
        if self.case_basis=="drive_document" and self.application!="drive":
            raise ValueError("document case grouping requires Drive")
        return self


def _grant(connection_id):
    """Inspect stored consent only; must not refresh access tokens."""
    if not isinstance(connection_id,str) or not CONNECTION.fullmatch(connection_id):
        raise rest.FeedError("google_grant_unavailable")
    try:
        connections=oauth.status()["connections"]
    except Exception:
        raise rest.FeedError("google_grant_unavailable") from None
    conn=next((c for c in connections if c.get("connection_id")==connection_id),None)
    if not conn or conn.get("provider")!="google":
        raise rest.FeedError("google_grant_unavailable")
    if set(conn.get("scopes",[]))!={SCOPE}:
        raise rest.FeedError("google_grant_scope_mismatch")
    if conn.get("reauthorization_required") or conn.get("has_refresh_token") is not True:
        raise rest.FeedError("google_reconnect_required")
    return "oauth_connection:"+connection_id


def check_binding(p):
    cfg=p["config"]
    if (cfg.get("application") not in APPLICATIONS or not CUSTOMER.fullmatch(cfg.get("customer_id",""))
            or cfg["customer_id"].lower()=="my_customer" or not USER.fullmatch(cfg.get("user_key",""))):
        raise rest.FeedError("google_invalid_saved_scope")
    identity=_grant(cfg["connection_id"])
    if not hmac.compare_digest(rest._key(p,"credential",identity),p["credential_binding"]):
        raise rest.FeedError("credential_changed_reconfigure_required")
    return identity


def create(value):
    try:
        spec=Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_google_audit_profile",422) from None
    if not spec.confirm_configuration or not spec.confirm_audit_retention:
        raise rest.FeedError("explicit_google_audit_configuration_and_retention_required",403)
    if spec.user_key=="all" and not spec.confirm_domain_scope:
        raise rest.FeedError("explicit_google_domain_wide_read_confirmation_required",403)
    if spec.case_basis=="drive_document" and not spec.confirm_drive_parameters:
        raise rest.FeedError("explicit_google_drive_parameter_read_confirmation_required",403)
    if spec.customer_id.lower()=="my_customer":
        raise rest.FeedError("exact_google_customer_id_required",422)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 for c in spec.name):
        raise rest.FeedError("invalid_profile_name",422)
    identity=_grant(spec.connection_id)
    now=rest._now()
    since=rest._stamp(spec.initial_since)
    if not now-timedelta(days=179)<=since<now-timedelta(minutes=spec.ingestion_lag_minutes):
        raise rest.FeedError("google_initial_since_outside_reviewable_window",422)
    config=spec.model_dump(exclude={"confirm_configuration","confirm_audit_retention","confirm_domain_scope","confirm_drive_parameters"})
    config.update(name=spec.name.strip(),initial_since=rest._iso(since))
    return rest.create_profile(config,identity,now,provider="google")


def review_cutoff(p,now,start):
    # Never silently jump over the provider's finite historical horizon.
    cutoff=now-timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    overlap=timedelta(minutes=p["config"]["overlap_minutes"]) if p["checkpoint"] else timedelta(0)
    if start-overlap<now-timedelta(days=180):
        raise rest.FeedError("google_checkpoint_older_than_provider_history")
    if cutoff<=start:
        raise rest.FeedError("no_new_checkpoint_window")
    return cutoff


def _opaque(value,max_length=200):
    return rest._text(value,max_length)


def _actor(item,cfg):
    actor=item.get("actor") or {}
    if not isinstance(actor,dict):
        raise rest.FeedError("invalid_google_actor",502)
    value=actor.get("profileId")
    if value in (None,"",PLACEHOLDER):
        # A scoped response must be attributable to exactly the selected user.
        if cfg["user_key"]!="all":
            raise rest.FeedError("google_selected_actor_not_verified",502)
        return None
    if not isinstance(value,str) or not re.fullmatch(r"[0-9]{5,64}",value):
        raise rest.FeedError("invalid_google_actor",502)
    if cfg["user_key"]!="all" and value!=cfg["user_key"]:
        raise rest.FeedError("google_selected_actor_mismatch",502)
    return value


def _doc_id(event):
    params=event.get("parameters",[])
    if not isinstance(params,list) or len(params)>300:
        raise rest.FeedError("invalid_google_drive_parameters",502)
    values=[]
    for param in params:
        if not isinstance(param,dict):
            raise rest.FeedError("invalid_google_drive_parameters",502)
        if param.get("name")=="doc_id":
            values.append(_opaque(param.get("value")))
    if len(set(values))>1:
        raise rest.FeedError("ambiguous_google_drive_document",502)
    return values[0] if values else None


def project(p,item,since,until):
    """Return one deterministic row per observed subevent, including repeats.

    Provider activity envelope identity is exact; subevent keys are local
    sorted occurrence indices, not provider-issued subevent identifiers.
    """
    cfg=p["config"]
    if not isinstance(item,dict) or not isinstance(item.get("id"),dict):
        raise rest.FeedError("invalid_google_activity",502)
    identity=item["id"]
    if identity.get("customerId")!=cfg["customer_id"] or identity.get("applicationName")!=cfg["application"]:
        raise rest.FeedError("google_customer_or_application_mismatch",502)
    qualifier=identity.get("uniqueQualifier")
    if not isinstance(qualifier,str) or not QUALIFIER.fullmatch(qualifier):
        raise rest.FeedError("invalid_google_activity_qualifier",502)
    at=rest._stamp(identity.get("time"))
    # Google may return an inclusive end boundary. Keep it as an observed
    # event; the next window deduplicates it using the stable envelope key.
    if not since<=at<=until:
        raise rest.FeedError("google_activity_outside_requested_window",502)
    stamp=rest._iso(at)
    envelope=rest._digest([cfg["customer_id"],cfg["application"],stamp,qualifier])
    actor=_actor(item,cfg)
    events=item.get("events")
    if not isinstance(events,list) or not 1<=len(events)<=100:
        raise rest.FeedError("invalid_google_subevents",502)
    projected=[]
    fallbacks=0
    for event in events:
        if not isinstance(event,dict):
            raise rest.FeedError("invalid_google_subevent",502)
        kind,name=event.get("type"),event.get("name")
        if not isinstance(kind,str) or not LABEL.fullmatch(kind) or not isinstance(name,str) or not LABEL.fullmatch(name):
            raise rest.FeedError("invalid_google_event_label",502)
        activity="google."+cfg["application"]+"."+kind+"."+name
        if cfg["case_basis"]=="drive_document":
            doc=_doc_id(event)
            case=rest._key(p,"google_document",doc) if doc else None
        elif cfg["case_basis"]=="actor":
            case=rest._key(p,"google_actor_case",actor) if actor else None
        else:
            case=None
        if case is None:
            case=rest._key(p,"google_envelope_case",envelope)
            if cfg["case_basis"]!="audit_record":
                fallbacks+=1
        resource=rest._key(p,"google_actor_resource",actor) if actor else ""
        projected.append((case,activity,stamp,resource))
    projected.sort()
    rows=[[rest._key(p,"google_event",envelope+":"+str(index)),*row]
          for index,row in enumerate(projected)]
    return envelope,rows,fallbacks


def _read_page(client,p,params,deadline):
    identity=rest._check(p)
    if identity!="oauth_connection:"+p["config"]["connection_id"]:
        raise rest.FeedError("google_grant_unavailable")
    try:
        token=oauth.bearer_for(p["config"]["connection_id"],"google")
    except oauth.OAuthError:
        raise rest.FeedError("google_reconnect_required") from None
    # Refresh is a separate authorized operation; check scope and profile
    # again after it completes before sending any Reports API request.
    rest._check(p)
    remaining=deadline-time.monotonic()
    if remaining<=0:
        raise rest.FeedError("read_time_budget_exceeded")
    cfg=p["config"]
    url=ROOT+cfg["user_key"]+"/applications/"+cfg["application"]
    try:
        with client.stream("GET",url,params=params,headers={"Authorization":"Bearer "+str(token),
                "Accept":"application/json"},timeout=min(10,remaining)) as response:
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
            raw=bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if time.monotonic()>deadline:
                    raise rest.FeedError("read_time_budget_exceeded")
                raw.extend(chunk)
                if len(raw)>rest.MAX_PAGE_BYTES:
                    raise rest.FeedError("provider_response_budget_exceeded")
    except httpx.HTTPError:
        raise rest.FeedError("provider_transport_failure",502) from None
    body=rest._strict_json(bytes(raw))
    if (not isinstance(body,dict) or set(body)-{"kind","etag","items","nextPageToken"}
            or body.get("kind") not in ("reports#activities","reports#auditActivities","admin#reports#activities")
            or not isinstance(body.get("items",[]),list)):
        raise rest.FeedError("invalid_google_report_page",502)
    return body,len(raw)


def extract(p,since,until,client_factory=httpx.Client):
    cfg=p["config"]
    event_fields="type,name,parameters(name,value)" if cfg["case_basis"]=="drive_document" else "type,name"
    params={"customerId":cfg["customer_id"],"startTime":rest._iso(since),"endTime":rest._iso(until),
            "maxResults":cfg["page_size"],
            "fields":"kind,nextPageToken,items(id(time,uniqueQualifier,applicationName,customerId),actor(profileId),events("+event_fields+"))"}
    # Never set includeSensitiveData. Gmail/Chat/Meet are audit metadata only.
    cursor=None
    seen=set()
    envelopes={}
    rows=[]
    total_bytes=received=duplicates=fallbacks=0
    deadline=time.monotonic()+rest.MAX_SECONDS
    with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
        for page in range(1,rest.MAX_PAGES+1):
            if cursor is not None:
                params["pageToken"]=cursor
            body,size=_read_page(client,p,params,deadline)
            total_bytes+=size
            if total_bytes>rest.MAX_TOTAL_BYTES:
                raise rest.FeedError("provider_response_budget_exceeded")
            items=body.get("items",[])
            if len(items)>cfg["page_size"]:
                raise rest.FeedError("provider_page_size_exceeded")
            received+=len(items)
            if received>rest.MAX_ROWS:
                raise rest.FeedError("provider_row_budget_exceeded")
            for item in items:
                envelope,batch,fallback=project(p,item,since,until)
                digest=rest._digest(batch)
                if envelope in envelopes:
                    if envelopes[envelope]!=digest:
                        raise rest.FeedError("conflicting_google_activity_envelope",502)
                    duplicates+=1
                    continue
                envelopes[envelope]=digest
                rows.extend(batch)
                fallbacks+=fallback
                if len(rows)>rest.MAX_ROWS:
                    raise rest.FeedError("projected_event_budget_exceeded")
            cursor=body.get("nextPageToken")
            if cursor is None:
                return rows,{"pages":page,"records_received":received,
                             "duplicate_records_in_window":duplicates,"audit_envelopes":len(envelopes),
                             "fallback_case_events":fallbacks,"customer_activity_readback_observed":bool(envelopes),
                             "provider_completeness_verified":False}
            if (not isinstance(cursor,str) or not 1<=len(cursor)<=1024
                    or any(ord(c)<33 or ord(c)>126 for c in cursor) or cursor in seen):
                raise rest.FeedError("invalid_or_repeated_provider_cursor",502)
            seen.add(cursor)
    raise rest.FeedError("provider_page_budget_exceeded")
