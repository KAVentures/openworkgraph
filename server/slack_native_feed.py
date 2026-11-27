"""Native Slack Enterprise audit and selected-channel activity feeds.

Only fixed Slack endpoints are used. Credential material is read from the
deployment environment and bound to the exact profile. Readback of auth.test
and selected channel scope is required before and after each complete window.
Message text, files, attachments, names, email, IP and audit details are never
retained. Channel history is not a complete conversation/thread archive.
"""
from datetime import datetime, timedelta, timezone
import hmac
import os
import re
import time
from typing import Literal

import httpx
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import enterprise_rest_feed as rest

FORMAT="openworkgraph.slack-native-feed.v1"
AUTH="https://slack.com/api/auth.test"
INFO="https://slack.com/api/conversations.info"
HISTORY="https://slack.com/api/conversations.history"
AUDIT="https://api.slack.com/audit/v1/logs"
ENV=re.compile(r"OWG_SLACK_TOKEN_[A-Z0-9_]{1,48}\Z")
ENTERPRISE=re.compile(r"E[A-Z0-9]{2,63}\Z")
TEAM=re.compile(r"T[A-Z0-9]{2,63}\Z")
CHANNEL=re.compile(r"[CG][A-Z0-9]{2,63}\Z")
USER=re.compile(r"[UW][A-Z0-9]{2,63}\Z")
ACT=re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")
TYP=re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
ID=re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
TS=re.compile(r"[0-9]{1,12}\.[0-9]{6}\Z")
EPOCH=datetime(1970,1,1,tzinfo=timezone.utc)
MAX_AUDIT_HISTORY_DAYS=366  # OWG's declared backfill bound, not a Slack retention promise.


class Create(rest.Strict):
    name: str=Field(min_length=1,max_length=100)
    mode: Literal["enterprise_audit","channel_activity"]
    credential_env: str=Field(pattern=r"^OWG_SLACK_TOKEN_[A-Z0-9_]{1,48}$")
    enterprise_id: str | None=Field(default=None,pattern=r"^E[A-Z0-9]{2,63}$")
    team_id: str | None=Field(default=None,pattern=r"^T[A-Z0-9]{2,63}$")
    channel_id: str | None=Field(default=None,pattern=r"^[CG][A-Z0-9]{2,63}$")
    case_basis: Literal["actor","entity","location","channel","thread"]
    actions: list[str]=Field(default_factory=list,max_length=30)
    actor_id: str | None=Field(default=None,pattern=r"^[UW][A-Z0-9]{2,63}$")
    initial_since: str=Field(min_length=20,max_length=40)
    window_hours: StrictInt=Field(ge=1,le=168,default=24)
    overlap_minutes: StrictInt=Field(ge=0,le=1440,default=60)
    ingestion_lag_minutes: StrictInt=Field(ge=0,le=1440,default=15)
    page_size: StrictInt=Field(ge=1,le=500,default=100)
    confirm_configuration: StrictBool
    confirm_retention: StrictBool
    confirm_org_audit: StrictBool=False
    confirm_transient_message_content: StrictBool=False

    @model_validator(mode="after")
    def consistent(self):
        if self.overlap_minutes>=self.window_hours*60:
            raise ValueError("overlap must be shorter than advancing window")
        if any(not ACT.fullmatch(a) for a in self.actions) or len(set(self.actions))!=len(self.actions):
            raise ValueError("invalid or duplicate audit action")
        if self.mode=="enterprise_audit":
            if not self.enterprise_id or self.team_id or self.channel_id or self.case_basis not in ("actor","entity","location"):
                raise ValueError("audit requires exact organization and audit case basis")
        else:
            if not self.team_id or not self.channel_id or self.actions or self.actor_id or self.case_basis not in ("channel","thread"):
                raise ValueError("history requires exact workspace, channel and channel/thread case basis")
        return self


def _credential(env,mode):
    if not isinstance(env,str) or not ENV.fullmatch(env):
        raise rest.FeedError("slack_invalid_credential_reference")
    value=os.environ.get(env,"")
    prefixes=("xoxp-",) if mode=="enterprise_audit" else ("xoxb-","xoxp-")
    if not value.startswith(prefixes) or not 12<=len(value)<=4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise rest.FeedError("slack_credential_unavailable")
    return value


def check_binding(p):
    cfg=p["config"]
    token=_credential(cfg["credential_env"],cfg["mode"])
    if not hmac.compare_digest(rest._key(p,"credential",token),p["credential_binding"]):
        raise rest.FeedError("credential_changed_reconfigure_required")
    return token


def create(value):
    try:
        spec=Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_slack_profile",422) from None
    if not spec.confirm_configuration or not spec.confirm_retention:
        raise rest.FeedError("explicit_slack_configuration_and_retention_required",403)
    if spec.mode=="enterprise_audit" and not spec.confirm_org_audit:
        raise rest.FeedError("explicit_slack_organization_audit_consent_required",403)
    if spec.mode=="channel_activity" and not spec.confirm_transient_message_content:
        raise rest.FeedError("explicit_slack_transient_message_read_consent_required",403)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 for c in spec.name):
        raise rest.FeedError("invalid_profile_name",422)
    token=_credential(spec.credential_env,spec.mode)
    now=rest._now()
    since=rest._stamp(spec.initial_since)
    if not now-timedelta(days=MAX_AUDIT_HISTORY_DAYS)<=since<now-timedelta(minutes=spec.ingestion_lag_minutes):
        raise rest.FeedError("slack_initial_since_outside_reviewable_window",422)
    if spec.mode=="enterprise_audit" and since.microsecond:
        raise rest.FeedError("slack_audit_start_requires_whole_second",422)
    cfg=spec.model_dump(exclude={"confirm_configuration","confirm_retention","confirm_org_audit","confirm_transient_message_content"})
    cfg.update(name=spec.name.strip(),initial_since=rest._iso(since),actions=sorted(spec.actions))
    return rest.create_profile(cfg,token,now,provider="slack")


def cutoff(p,now,start):
    result=now-timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    if p["config"]["mode"]=="enterprise_audit":
        result=result.replace(microsecond=0)
    if result<=start:
        raise rest.FeedError("no_new_checkpoint_window")
    return result


def _id(value,pattern=ID):
    if not isinstance(value,str) or not pattern.fullmatch(value):
        raise rest.FeedError("invalid_slack_source_identity",502)
    return value


def _epoch_us(at):
    delta=at-EPOCH
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def _decimal_ts(at):
    us=_epoch_us(at)
    return f"{us//1000000}.{us%1000000:06d}"


def _parse_ts(value):
    if not isinstance(value,str) or not TS.fullmatch(value):
        raise rest.FeedError("invalid_slack_message_timestamp",502)
    whole,fraction=value.split(".")
    try:
        at=EPOCH+timedelta(seconds=int(whole),microseconds=int(fraction))
        if not 2000<=at.year<=2100 or _decimal_ts(at)!=value:
            raise ValueError()
        return at
    except (ValueError,OverflowError):
        raise rest.FeedError("invalid_slack_message_timestamp",502) from None


def _audit_time(value):
    if type(value) is not int:
        raise rest.FeedError("invalid_slack_audit_timestamp",502)
    try:
        at=EPOCH+timedelta(seconds=value)
        if not 2018<=at.year<=2100:
            raise ValueError()
        return at
    except (ValueError,OverflowError):
        raise rest.FeedError("invalid_slack_audit_timestamp",502) from None


def _typed(value):
    if not isinstance(value,dict):
        raise rest.FeedError("invalid_slack_typed_object",502)
    kind=_id(value.get("type"),TYP)
    obj=value.get(kind)
    if obj is None:
        return kind,None
    if not isinstance(obj,dict):
        raise rest.FeedError("invalid_slack_typed_object",502)
    key=obj.get("id")
    return kind,_id(key) if key is not None else None


def project_audit(p,item,since,until):
    if not isinstance(item,dict):
        raise rest.FeedError("invalid_slack_audit_event",502)
    cfg=p["config"]
    event=_id(item.get("id"))
    action=_id(item.get("action"),ACT)
    at=_audit_time(item.get("date_create"))
    if not since<=at<=until:
        raise rest.FeedError("slack_event_outside_requested_window",502)
    if cfg["actions"] and action not in cfg["actions"]:
        raise rest.FeedError("slack_action_filter_mismatch",502)
    kind,actor=_typed(item.get("actor"))
    if kind!="user":
        raise rest.FeedError("invalid_slack_audit_actor",502)
    if actor is not None:
        _id(actor,USER)
    if cfg["actor_id"] and actor!=cfg["actor_id"]:
        raise rest.FeedError("slack_actor_filter_mismatch",502)
    entity_kind,entity=_typed(item.get("entity"))
    context=item.get("context")
    if not isinstance(context,dict) or not isinstance(context.get("location"),dict):
        raise rest.FeedError("invalid_slack_audit_location",502)
    location=context["location"]
    loc_type=location.get("type")
    if loc_type=="enterprise":
        loc=_id(location.get("id"),ENTERPRISE)
        if loc!=cfg["enterprise_id"]:
            raise rest.FeedError("slack_enterprise_mismatch",502)
    elif loc_type=="workspace":
        # A workspace ID in an organization-scoped audit response does not
        # independently establish its membership; auth.test pins the grant.
        loc=_id(location.get("id"),TEAM)
    else:
        raise rest.FeedError("invalid_slack_audit_location",502)
    if actor in ("USLACKUSER","USLACKSECURITY"):
        actor=None
    basis=cfg["case_basis"]
    value=actor if basis=="actor" else (entity_kind+":"+entity if entity else None) if basis=="entity" else loc_type+":"+loc
    fallback=value is None
    case=rest._key(p,"slack_"+basis,value) if value else rest._key(p,"slack_event_case",event)
    resource=rest._key(p,"slack_actor",actor) if actor else ""
    return [rest._key(p,"slack_audit_event",event),case,"slack.audit."+action,rest._iso(at),resource],fallback


def project_message(p,item,since,until):
    if not isinstance(item,dict) or item.get("type")!="message":
        raise rest.FeedError("invalid_slack_history_message",502)
    cfg=p["config"]
    stamp=item.get("ts")
    at=_parse_ts(stamp)
    if not since<=at<=until:
        raise rest.FeedError("slack_event_outside_requested_window",502)
    subtype=item.get("subtype") or "message"
    _id(subtype,ACT)
    # Hidden mutation notices are not ordinary message creation evidence.
    # Do not silently drop them or synthesize a deletion/edit event.
    if item.get("hidden") is True or subtype in ("message_changed","message_deleted"):
        raise rest.FeedError("slack_history_mutation_notice_unsupported",502)
    thread=item.get("thread_ts") or stamp
    if _parse_ts(thread)>at:
        raise rest.FeedError("invalid_slack_thread_timestamp",502)
    user=item.get("user")
    bot=item.get("bot_id")
    if user is not None:
        actor="user:"+_id(user,USER)
    elif bot is not None:
        actor="bot:"+_id(bot,re.compile(r"B[A-Z0-9]{2,63}\Z"))
    else:
        actor=None
    channel=cfg["channel_id"]
    value=channel if cfg["case_basis"]=="channel" else channel+":"+thread
    return [rest._key(p,"slack_message",channel+":"+stamp),rest._key(p,"slack_"+cfg["case_basis"],value),
            "slack.channel."+subtype,rest._iso(at),rest._key(p,"slack_actor",actor) if actor else ""],False


class Reader:
    def __init__(self,p,client):
        self.p=p
        self.client=client
        self.deadline=time.monotonic()+rest.MAX_SECONDS
        self.bytes=0
        self.requests=0

    def get(self,url,params=None):
        token=rest._check(self.p)
        left=self.deadline-time.monotonic()
        if left<=0:
            raise rest.FeedError("read_time_budget_exceeded")
        self.requests+=1
        try:
            with self.client.stream("GET",url,params=params,headers={"Authorization":"Bearer "+token,
                    "Accept":"application/json"},timeout=min(10,left)) as response:
                if response.status_code in (401,403):
                    raise rest.FeedError("slack_source_access_denied",502)
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
                    if time.monotonic()>self.deadline:
                        raise rest.FeedError("read_time_budget_exceeded")
                    raw.extend(chunk)
                    if len(raw)>rest.MAX_PAGE_BYTES or self.bytes+len(raw)>rest.MAX_TOTAL_BYTES:
                        raise rest.FeedError("provider_response_budget_exceeded")
                self.bytes+=len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure",502) from None
        body=rest._strict_json(bytes(raw))
        if not isinstance(body,dict):
            raise rest.FeedError("invalid_slack_page",502)
        if body.get("ok") is False or "error" in body:
            if body.get("error") in ("ratelimited","rate_limited"):
                raise rest.FeedError("provider_rate_limited",429,60)
            raise rest.FeedError("slack_source_request_refused",502)
        return body

    def scope(self):
        cfg=self.p["config"]
        identity=self.get(AUTH)
        if identity.get("ok") is not True:
            raise rest.FeedError("slack_auth_readback_unverified",502)
        user=_id(identity.get("user_id"),USER)
        if cfg["mode"]=="enterprise_audit":
            if identity.get("enterprise_id")!=cfg["enterprise_id"]:
                raise rest.FeedError("slack_enterprise_mismatch",502)
        else:
            if identity.get("team_id")!=cfg["team_id"] or identity.get("enterprise_id")!=cfg["enterprise_id"]:
                raise rest.FeedError("slack_workspace_mismatch",502)
            body=self.get(INFO,{"channel":cfg["channel_id"]})
            ch=body.get("channel")
            if (body.get("ok") is not True or not isinstance(ch,dict) or ch.get("id")!=cfg["channel_id"]
                    or ch.get("is_member") is not True or ch.get("is_ext_shared") is not False
                    or ch.get("is_shared") is not False or ch.get("is_org_shared") is not False
                    or ch.get("is_im") is not False or ch.get("is_mpim") is not False
                    or not (ch.get("is_channel") is True or ch.get("is_group") is True)):
                raise rest.FeedError("slack_channel_scope_not_verified",502)
        return user

    def pages(self,since,until):
        cfg=self.p["config"]
        audit=cfg["mode"]=="enterprise_audit"
        if audit:
            url=AUDIT
            params={"oldest":_epoch_us(since)//1000000,"latest":_epoch_us(until)//1000000,"limit":cfg["page_size"]}
            if cfg["actions"]:
                params["action"]=",".join(cfg["actions"])
            if cfg["actor_id"]:
                params["actor"]=cfg["actor_id"]
        else:
            url=HISTORY
            params={"channel":cfg["channel_id"],"oldest":_decimal_ts(since),"latest":_decimal_ts(until),
                    "inclusive":"true","limit":cfg["page_size"]}
        seen=set()
        rows={}
        received=duplicates=fallbacks=0
        for index in range(1,rest.MAX_PAGES+1):
            body=self.get(url,params)
            records=body.get("entries" if audit else "messages")
            if (not audit and body.get("ok") is not True) or not isinstance(records,list):
                raise rest.FeedError("invalid_slack_page",502)
            # Slack audit can return more than limit at a shared timestamp.
            # Honor global caps instead of truncating provider evidence.
            if not audit and len(records)>cfg["page_size"]:
                raise rest.FeedError("provider_page_size_exceeded")
            received+=len(records)
            if received>rest.MAX_ROWS:
                raise rest.FeedError("provider_row_budget_exceeded")
            for item in records:
                row,fallback=(project_audit if audit else project_message)(self.p,item,since,until)
                if row[0] in rows:
                    if rows[row[0]]!=row:
                        raise rest.FeedError("conflicting_source_event_identity",502)
                    duplicates+=1
                    continue
                rows[row[0]]=row
                fallbacks+=fallback
            metadata=body.get("response_metadata") or {}
            if not isinstance(metadata,dict):
                raise rest.FeedError("invalid_slack_cursor_metadata",502)
            cursor=metadata.get("next_cursor")
            if cursor in (None,""):
                if body.get("has_more") is True:
                    raise rest.FeedError("slack_more_without_cursor",502)
                return list(rows.values()),{"pages":index,"records_received":received,
                        "duplicate_records_in_window":duplicates,"fallback_case_events":fallbacks}
            if (not isinstance(cursor,str) or not 1<=len(cursor)<=1024
                    or any(ord(c)<33 or ord(c)>126 for c in cursor) or cursor in seen):
                raise rest.FeedError("invalid_or_repeated_provider_cursor",502)
            seen.add(cursor)
            params["cursor"]=cursor
        raise rest.FeedError("provider_page_budget_exceeded")


def extract(p,since,until,client_factory=httpx.Client):
    with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
        reader=Reader(p,client)
        before=reader.scope()
        principal_pin=rest._key(p,"slack_principal",before)
        if p.get("slack_principal_pin") not in (None,principal_pin):
            raise rest.FeedError("slack_principal_changed_during_read",502)
        rows,counts=reader.pages(since,until)
        after=reader.scope()
        if before!=after:
            raise rest.FeedError("slack_principal_changed_during_read",502)
        counts.update(requests=reader.requests,scope_readback_verified=True,
                      _slack_principal_pin=principal_pin,
                      provider_completeness_verified=False,
                      thread_replies_separately_traversed=False,message_content_retained=False)
        return rows,counts
