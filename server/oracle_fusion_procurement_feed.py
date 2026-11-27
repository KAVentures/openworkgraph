"""Oracle Fusion procurement: exact selected header lifecycle observations.

Read only the documented requisition and purchase-order header resources.
Mutable submission/communication/update dates are observed snapshot facts,
not a complete approval or change journal. Never infer transitions from status.
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

CATALOG = "OWG_ORACLE_FUSION_CATALOG"
ALIAS = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
HOST = re.compile(r"[a-z0-9][a-z0-9-]{0,100}\.fa\.[a-z0-9-]{1,60}\.oraclecloud\.com\Z")
USER = re.compile(r"[A-Za-z0-9_.@-]{1,128}\Z")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")
BASE = "/fscmRestApi/resources/11.13.18.05/"
MAX_ID = 2**63 - 1
# Exact stable header key, business-unit key, and documented date fields.
# OrderDate is a communication date, not approval or creation.
RESOURCES = {
    "requisition": ("purchaseRequisitions", "RequisitionHeaderId", "RequisitioningBUId",
                    "SubmissionDate", "submission_date_observed"),
    "purchase_order": ("purchaseOrders", "POHeaderId", "ProcurementBUId",
                       "OrderDate", "communication_date_observed"),
}


def require(ok, code, status=502):
    if not ok:
        raise rest.FeedError(code, status)


class Source(rest.Strict):
    host: str = Field(max_length=180)
    integration_user: str = Field(max_length=128)
    credential_env: str = Field(pattern=r"^OWG_ORACLE_FUSION_PASSWORD_[A-Z0-9_]{1,48}$")
    requisition_business_units: list[StrictInt] = Field(default_factory=list, max_length=30)
    purchase_order_business_units: list[StrictInt] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def reviewed(self):
        if not HOST.fullmatch(self.host) or not USER.fullmatch(self.integration_user):
            raise ValueError("fixed Oracle Fusion account host and service user required")
        for values in (self.requisition_business_units, self.purchase_order_business_units):
            if len(set(values)) != len(values) or any(not 1 <= v <= MAX_ID for v in values):
                raise ValueError("distinct exact business unit IDs required")
        if not self.requisition_business_units and not self.purchase_order_business_units:
            raise ValueError("approved business units required")
        self.requisition_business_units.sort()
        self.purchase_order_business_units.sort()
        return self


class Selection(rest.Strict):
    object_type: Literal["requisition", "purchase_order"]
    business_unit_id: StrictInt = Field(ge=1, le=MAX_ID)
    # String wire IDs preserve 64-bit precision in browser JavaScript.
    header_ids: list[str] = Field(min_length=1, max_length=9)
    include_created: StrictBool = True
    include_milestone: StrictBool = True
    include_latest_update: StrictBool = False

    @model_validator(mode="after")
    def reviewed(self):
        if (any(not isinstance(v,str) or not re.fullmatch(r"[1-9][0-9]{0,18}",v)
                or int(v)>MAX_ID for v in self.header_ids)
                or len(set(self.header_ids)) != len(self.header_ids)
                or not any((self.include_created,self.include_milestone,self.include_latest_update))):
            raise ValueError("exact distinct header IDs and selected timestamp evidence required")
        self.header_ids.sort(key=int)
        return self


class Create(rest.Strict):
    name: str = Field(min_length=1, max_length=100)
    source_alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selections: list[Selection] = Field(min_length=1, max_length=2)
    initial_since: str = Field(min_length=20, max_length=40)
    window_hours: StrictInt = Field(ge=1, le=168, default=24)
    overlap_minutes: StrictInt = Field(ge=0, le=1440, default=60)
    ingestion_lag_minutes: StrictInt = Field(ge=0, le=1440, default=15)
    confirm_configuration: StrictBool
    confirm_source_read: StrictBool
    confirm_snapshot_semantics: StrictBool
    confirm_metadata_retention: StrictBool

    @model_validator(mode="after")
    def reviewed(self):
        if (self.overlap_minutes >= self.window_hours * 60
                or len({s.object_type for s in self.selections}) != len(self.selections)
                or 2 * sum(len(s.header_ids) for s in self.selections) > rest.MAX_PAGES):
            raise ValueError("duplicate resource, invalid overlap or request budget exceeded")
        return self


def catalog():
    try:
        raw = os.environ.get(CATALOG, "{}")
        if len(raw) > 100000:
            raise ValueError()
        data = rest._strict_json(raw.encode())
        if not isinstance(data, dict) or len(data)>30 or any(not ALIAS.fullmatch(a) for a in data):
            raise ValueError()
        return {alias: Source.model_validate(v).model_dump() for alias,v in data.items()}
    except (ValueError,TypeError,rest.FeedError):
        raise rest.FeedError("invalid_oracle_fusion_catalog",422) from None


def password(src):
    value = rest.resolve_secret(src["credential_env"])
    require(isinstance(value,str) and 1<=len(value)<=1024
            and all(32<=ord(c)<=126 for c in value) and value.strip(),
            "oracle_fusion_credential_unavailable",409)
    return value


def available(src):
    try:
        password(src)
        return True
    except rest.FeedError:
        return False


def catalog_public():
    return {"sources":[{"source_alias":a,"source_sha256":rest._digest(v),
                        "requisition_business_units":[str(x) for x in v["requisition_business_units"]],
                        "purchase_order_business_units":[str(x) for x in v["purchase_order_business_units"]],
                        "credential_available":available(v)} for a,v in sorted(catalog().items())],
            "provider_reads_performed":False}


def source(cfg):
    value = catalog().get(cfg["source_alias"])
    require(value is not None and rest._digest(value)==cfg["source_sha256"],
            "oracle_fusion_source_definition_changed",409)
    for s in cfg["selections"]:
        require(int(s["business_unit_id"]) in value[s["object_type"]+"_business_units"],
                "oracle_fusion_business_unit_not_approved",403)
    return value


def check_binding(p):
    secret = password(source(p["config"]))
    require(hmac.compare_digest(rest._key(p,"credential",secret),p["credential_binding"]),
            "credential_changed_reconfigure_required",409)
    return secret


def create(value):
    # JSON clients must send business-unit IDs as decimal strings, so browser
    # numbers cannot silently round large Oracle IDs. Internal model uses int.
    if not isinstance(value,dict) or not isinstance(value.get("selections"),list):
        raise rest.FeedError("invalid_oracle_fusion_profile",422)
    value = dict(value)
    selections = []
    for row in value["selections"]:
        if not isinstance(row,dict):
            raise rest.FeedError("invalid_oracle_fusion_profile",422)
        bu = row.get("business_unit_id")
        require(isinstance(bu,str) and re.fullmatch(r"[1-9][0-9]{0,18}",bu)
                and int(bu)<=MAX_ID,"invalid_oracle_fusion_business_unit_id",422)
        selections.append({**row,"business_unit_id":int(bu)})
    value["selections"] = selections
    try:
        spec = Create.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_oracle_fusion_profile",422) from None
    require(all((spec.confirm_configuration,spec.confirm_source_read,
                 spec.confirm_snapshot_semantics,spec.confirm_metadata_retention)),
            "explicit_oracle_fusion_snapshot_and_retention_required",403)
    require(spec.name.strip() and all(ord(c)>=32 and ord(c)!=127
            and not 0xD800<=ord(c)<=0xDFFF for c in spec.name),"invalid_profile_name",422)
    now = rest._now()
    since = rest._stamp(spec.initial_since)
    require(now-timedelta(days=366)<=since<now-timedelta(minutes=spec.ingestion_lag_minutes),
            "oracle_fusion_initial_since_outside_reviewed_window",422)
    cfg = spec.model_dump(exclude={"confirm_configuration","confirm_source_read",
                                  "confirm_snapshot_semantics","confirm_metadata_retention"})
    cfg.update(name=spec.name.strip(),initial_since=rest._iso(since),
               selections=sorted(cfg["selections"],key=lambda x:x["object_type"]))
    for s in cfg["selections"]:
        s["business_unit_id"]=str(s["business_unit_id"])
    return rest.create_profile(cfg,password(source(cfg)),now,provider="oracle_fusion")


def cutoff(p,now,start):
    until=now-timedelta(minutes=p["config"]["ingestion_lag_minutes"])
    require(until>start,"no_new_checkpoint_window",409)
    return until


def fields(selection):
    _,identity,bu,milestone,_ = RESOURCES[selection["object_type"]]
    result = [identity,bu,"CreationDate"]
    if selection["include_milestone"]:
        result.append(milestone)
    if selection["include_latest_update"]:
        result.append("LastUpdateDate")
    return result


def stamp(value, optional=False):
    if value is None and optional:
        return None
    require(isinstance(value,str) and STAMP.fullmatch(value) is not None,
            "oracle_fusion_invalid_timestamp")
    return rest._stamp(value)


def project(p,selection,header_id,item,since,until):
    kind=selection["object_type"]
    _,identity,bu,milestone,label=RESOURCES[kind]
    require(isinstance(item,dict) and set(item)==set(fields(selection)),
            "oracle_fusion_header_schema_mismatch")
    require(type(item[identity]) is int and item[identity]==int(header_id)
            and type(item[bu]) is int and item[bu]==int(selection["business_unit_id"]),
            "oracle_fusion_header_or_business_unit_mismatch")
    created=stamp(item["CreationDate"])
    dates=[]
    if selection["include_created"]:
        dates.append(("created",created))
    if selection["include_milestone"]:
        at=stamp(item[milestone],optional=True)
        if at is not None:
            require(at>=created,"oracle_fusion_inconsistent_header_dates")
            dates.append((label,at))
    if selection["include_latest_update"]:
        at=stamp(item["LastUpdateDate"])
        require(at>=created,"oracle_fusion_inconsistent_header_dates")
        # A creation timestamp is not evidence of a separate later update.
        if at>created:
            dates.append(("latest_update_observed",at))
    native=kind+":"+str(selection["business_unit_id"])+":"+header_id
    case=rest._key(p,"oracle_fusion_case",native)
    rows=[]
    for action,at in dates:
        if since<=at<until:
            # Date observations are timestamp-specific. A subsequently changed
            # date remains a separately observed historical fact, not a correction.
            event=rest._key(p,"oracle_fusion_event",native+":"+action+":"+rest._iso(at))
            rows.append([event,case,"oracle_fusion."+kind+"."+action,rest._iso(at),""])
    # Compare complete selected timestamp facts, including out-of-window values.
    return rows, [(k,rest._iso(v)) for k,v in dates],rest._iso(created)


class Reader:
    def __init__(self,p,client,credential_check=rest._check):
        self.p,self.client,self.credential_check=p,client,credential_check
        self.src=source(p["config"])
        self.pages=self.bytes=0
        self.deadline=time.monotonic()+rest.MAX_SECONDS

    def header(self,selection,header_id):
        require(self.pages<rest.MAX_PAGES,"provider_page_budget_exceeded",409)
        self.pages+=1
        secret=self.credential_check(self.p)
        remaining=self.deadline-time.monotonic()
        require(remaining>0,"read_time_budget_exceeded",409)
        path,identity,bu,_,_=RESOURCES[selection["object_type"]]
        # Fixed query with two validated integer predicates. No raw q input.
        params={"q":f"{identity}={int(header_id)};{bu}={selection['business_unit_id']}",
                "fields":",".join(fields(selection)),"onlyData":"true","limit":"2","offset":"0"}
        try:
            with self.client.stream("GET","https://"+self.src["host"]+BASE+path,
                                    params=params,auth=httpx.BasicAuth(self.src["integration_user"],secret),
                                    headers={"Accept":"application/json","REST-Framework-Version":"4"},
                                    timeout=min(20,remaining)) as response:
                if response.status_code==429:
                    try:
                        delay=max(60,min(86400,int(response.headers.get("Retry-After","60"))))
                    except ValueError:
                        delay=60
                    raise rest.FeedError("provider_rate_limited",429,delay)
                require(response.status_code==200,"oracle_fusion_http_failure")
                raw=bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    raw.extend(part)
                    require(len(raw)<=rest.MAX_PAGE_BYTES and self.bytes+len(raw)<=rest.MAX_TOTAL_BYTES
                            and time.monotonic()<=self.deadline,"provider_response_budget_exceeded")
                self.bytes+=len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure",502) from None
        result=rest._strict_json(bytes(raw))
        require(isinstance(result,dict) and isinstance(result.get("items"),list)
                and len(result["items"])==1 and type(result.get("count")) is int
                and result["count"]==1 and result.get("hasMore") is False
                and type(result.get("offset")) is int and result["offset"]==0,
                "oracle_fusion_exact_header_not_visible_or_ambiguous")
        return result["items"][0]

    def scan(self,since,until):
        rows, facts=[],[]
        for selection in self.p["config"]["selections"]:
            for header_id in selection["header_ids"]:
                batch,dates,creation=project(self.p,selection,header_id,
                                             self.header(selection,header_id),since,until)
                facts.append([selection["object_type"],header_id,dates,creation])
                rows.extend(batch)
        require(len(rows)<=rest.MAX_ROWS and len({r[0] for r in rows})==len(rows),
                "oracle_fusion_duplicate_or_oversized_events")
        return rows,facts


def extract(p,since,until,client_factory=httpx.Client):
    with client_factory(timeout=20,follow_redirects=False,trust_env=False) as client:
        reader=Reader(p,client)
        first,facts=reader.scan(since,until)
        second,recheck=reader.scan(since,until)
        require(first==second and facts==recheck,"oracle_fusion_header_snapshot_changed")
        reader.credential_check(p)
        return first,{"pages":reader.pages,"records_received":2*len(facts),
                      "selected_headers_observed":len(facts),"duplicate_records_in_window":0,
                      "repeat_scan_agreement":True,"transaction_snapshot_verified":False,
                      "provider_completeness_verified":False,"independent_principal_readback_verified":False,
                      "mutable_header_dates_are_observations":True,"raw_source_identifiers_retained":False}
