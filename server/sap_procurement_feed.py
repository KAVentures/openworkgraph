"""SAP S/4HANA purchase-order-centric daily evidence over native OData V2.

The PO selection is exact. Material and invoice header traversals can contain
unrelated references transiently; only explicitly selected PO references are
retained. Business dates are day precision, never claimed execution times.
"""
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import base64
import hmac
import json
import os
import re
import time
from urllib.parse import urlsplit,parse_qsl,urljoin
from typing import Literal

import httpx
from pydantic import Field,StrictBool,StrictInt,ValidationError,model_validator
from . import enterprise_rest_feed as rest

SOURCES={
 "purchase_orders":("API_PURCHASEORDER_PROCESS_SRV","A_PurchaseOrder","CreationDate",("PurchaseOrder",),None,()),
 "material_movements":("API_MATERIAL_DOCUMENT_SRV","A_MaterialDocumentHeader","PostingDate",
    ("MaterialDocumentYear","MaterialDocument"),"to_MaterialDocumentItem",
    ("MaterialDocumentYear","MaterialDocument","MaterialDocumentItem","PurchaseOrder","PurchaseOrderItem","GoodsMovementType")),
 "supplier_invoices":("API_SUPPLIERINVOICE_PROCESS_SRV","A_SupplierInvoice","PostingDate",
    ("FiscalYear","SupplierInvoice"),"to_SuplrInvcItemPurOrdRef",
    ("FiscalYear","SupplierInvoice","SupplierInvoiceItem","PurchaseOrder","PurchaseOrderItem")),
}
Kind=Literal["purchase_orders","material_movements","supplier_invoices"]
ALIAS=re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
ENV=re.compile(r"OWG_SAP_P2P_[A-Z0-9_]{1,48}\Z")
DOC=re.compile(r"[0-9]{1,10}\Z")
ITEM=re.compile(r"[0-9]{1,6}\Z")
YEAR=re.compile(r"[0-9]{4}\Z")
MOVE=re.compile(r"[0-9]{3}\Z")
DIGEST=re.compile(r"[a-f0-9]{64}\Z")
DATE=re.compile(r"/Date\(([0-9]{1,15})\)/\Z")
ROOT=re.compile(r"https://[a-z0-9](?:[a-z0-9.-]{0,250}[a-z0-9])?(?::[0-9]{1,5})?/sap/opu/odata/sap\Z")
CATALOG_ENV="OWG_SAP_P2P_CATALOG"


class Source(rest.Strict):
    service_root:str=Field(max_length=320)
    sap_client:str=Field(pattern=r"^[0-9]{3}$")
    auth_type:Literal["bearer","basic"]="bearer"
    credential_env:str=Field(pattern=r"^OWG_SAP_P2P_[A-Z0-9_]{1,48}$")
    metadata_sha256:dict[Kind,str]

    @model_validator(mode="after")
    def valid(self):
        try:
            parts=urlsplit(self.service_root)
            if (not ROOT.fullmatch(self.service_root) or not parts.hostname or ".." in parts.hostname
                or "." not in parts.hostname or parts.port not in (None,443)):
                raise ValueError()
        except ValueError:
            raise ValueError("fixed HTTPS SAP service root required") from None
        if not self.metadata_sha256 or any(not DIGEST.fullmatch(v) for v in self.metadata_sha256.values()):
            raise ValueError("exact metadata digests required")
        return self


class Create(rest.Strict):
    name:str=Field(min_length=1,max_length=100)
    source_alias:str=Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    source_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    purchase_orders:list[str]=Field(min_length=1,max_length=100)
    streams:list[Kind]=Field(min_length=1,max_length=3)
    initial_since:str=Field(min_length=20,max_length=40)
    window_days:StrictInt=Field(ge=1,le=7,default=1)
    overlap_days:StrictInt=Field(ge=0,le=1,default=0)
    lag_days:StrictInt=Field(ge=0,le=7,default=1)
    page_size:StrictInt=Field(ge=1,le=500,default=100)
    confirm_configuration:StrictBool
    confirm_day_precision:StrictBool
    confirm_retention:StrictBool
    confirm_transient_unselected_references:StrictBool=False

    @model_validator(mode="after")
    def valid(self):
        if any(not DOC.fullmatch(v) for v in self.purchase_orders):
            raise ValueError("invalid purchase order number")
        if len(set(po(v) for v in self.purchase_orders))!=len(self.purchase_orders):
            raise ValueError("duplicate purchase orders")
        if len(set(self.streams))!=len(self.streams):
            raise ValueError("duplicate source streams")
        return self


def po(v):
    if not isinstance(v,str) or not DOC.fullmatch(v) or int(v)==0:
        raise rest.FeedError("invalid_sap_purchase_order",422)
    return v.zfill(10)


def key(v,pattern):
    if not isinstance(v,str) or not pattern.fullmatch(v):
        raise rest.FeedError("invalid_sap_source_identity",502)
    return v


def catalog():
    try:
        raw=os.environ.get(CATALOG_ENV,"{}")
        if len(raw)>100000:
            raise ValueError()
        obj=rest._strict_json(raw.encode())
        if not isinstance(obj,dict) or len(obj)>30 or any(not ALIAS.fullmatch(a) for a in obj):
            raise ValueError()
        return {a:Source.model_validate(v).model_dump() for a,v in obj.items()}
    except (ValueError,TypeError,ValidationError,rest.FeedError):
        raise rest.FeedError("invalid_sap_deployment_catalog",422) from None


def catalog_public():
    return {"sources":[{"source_alias":a,"source_sha256":rest._digest(v),"service_root":v["service_root"],
                        "sap_client":v["sap_client"],"streams":sorted(v["metadata_sha256"]),
                        "credential_available":available(v)} for a,v in sorted(catalog().items())],
            "provider_reads_performed":False}


def token(src):
    raw=os.environ.get(src["credential_env"],"")
    if not 8<=len(raw)<=4096 or any(ord(c)<33 or ord(c)>126 for c in raw):
        raise rest.FeedError("sap_credential_unavailable")
    if src["auth_type"]=="basic":
        if ":" not in raw or not all(raw.split(":",1)):
            raise rest.FeedError("sap_credential_unavailable")
        return "Basic "+base64.b64encode(raw.encode()).decode()
    return "Bearer "+raw


def available(src):
    try:
        token(src)
        return True
    except rest.FeedError:
        return False


def source(cfg):
    value=catalog().get(cfg["source_alias"])
    if value is None or rest._digest(value)!=cfg["source_sha256"]:
        raise rest.FeedError("sap_source_definition_changed")
    if any(s not in value["metadata_sha256"] for s in cfg["streams"]):
        raise rest.FeedError("sap_source_definition_changed")
    return value


def check_binding(p):
    cfg=p["config"];src=source(cfg);secret=token(src)
    if not hmac.compare_digest(rest._key(p,"credential",secret),p["credential_binding"]):
        raise rest.FeedError("credential_changed_reconfigure_required")
    return secret


def midnight(at):
    return at.replace(hour=0,minute=0,second=0,microsecond=0)


def create(value):
    try:
        spec=Create.model_validate(value)
        orders=sorted(po(v) for v in spec.purchase_orders)
    except (ValidationError,rest.FeedError):
        raise rest.FeedError("invalid_sap_procurement_profile",422) from None
    if not spec.confirm_configuration or not spec.confirm_day_precision or not spec.confirm_retention:
        raise rest.FeedError("explicit_sap_configuration_precision_retention_required",403)
    if any(s!="purchase_orders" for s in spec.streams) and not spec.confirm_transient_unselected_references:
        raise rest.FeedError("explicit_sap_transient_reference_read_required",403)
    if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in spec.name):
        raise rest.FeedError("invalid_profile_name",422)
    now=rest._now();since=rest._stamp(spec.initial_since)
    if since!=midnight(since) or not midnight(now)-timedelta(days=365)<=since<midnight(now)-timedelta(days=spec.lag_days):
        raise rest.FeedError("sap_initial_since_requires_completed_utc_day_within_365_days",422)
    cfg=spec.model_dump(exclude={"confirm_configuration","confirm_day_precision","confirm_retention",
                                 "confirm_transient_unselected_references","window_days","overlap_days"})
    cfg.update(name=spec.name.strip(),purchase_orders=orders,streams=sorted(spec.streams),
               initial_since=rest._iso(since),window_hours=24*spec.window_days,overlap_minutes=1440*spec.overlap_days)
    src=source(cfg)
    return rest.create_profile(cfg,token(src),now,provider="sap")


def cutoff(p,now,start):
    result=midnight(now)-timedelta(days=p["config"]["lag_days"])
    if result<=start:
        raise rest.FeedError("no_new_checkpoint_window")
    return result


def day(raw):
    # OData V2 Edm.DateTime date-only values. Reject offsets and non-midnight
    # rather than silently shifting SAP's declared business date.
    if not isinstance(raw,str) or not DATE.fullmatch(raw):
        raise rest.FeedError("invalid_sap_day_value",502)
    try:
        ms=int(DATE.fullmatch(raw)[1])
        if ms%86400000:
            raise ValueError()
        at=datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(milliseconds=ms)
        if not 1900<=at.year<=2100:
            raise ValueError()
        return at
    except (ValueError,OverflowError):
        raise rest.FeedError("invalid_sap_day_value",502) from None


def query(cfg,kind,since,until):
    service,entity,date,keys,nav,items=SOURCES[kind]
    fields=list(keys)+[date]
    filters=[date+" ge datetime'"+since.strftime("%Y-%m-%dT00:00:00")+"'",
             date+" lt datetime'"+until.strftime("%Y-%m-%dT00:00:00")+"'"]
    if kind=="purchase_orders":
        filters.append("("+" or ".join("PurchaseOrder eq '"+po(v)+"'" for v in cfg["purchase_orders"])+")")
    if nav:
        fields.extend(nav+"/"+v for v in items)
    params={"$format":"json","$select":",".join(fields),"$filter":" and ".join(filters),
            "$orderby":",".join((date,*keys)),"$top":str(cfg["page_size"]),"$inlinecount":"allpages"}
    if nav:
        params["$expand"]=nav
    return params


def collection(value):
    if not isinstance(value,dict) or not isinstance(value.get("results"),list):
        raise rest.FeedError("invalid_sap_odata_collection",502)
    return value["results"]


def project(p,kind,item,since,until):
    _,_,date,keys,nav,_=SOURCES[kind]
    if not isinstance(item,dict):
        raise rest.FeedError("invalid_sap_header",502)
    at=day(item.get(date))
    if not since<=at<until:
        raise rest.FeedError("sap_header_outside_requested_day",502)
    selected=set(p["config"]["purchase_orders"])
    if kind=="purchase_orders":
        number=po(item.get("PurchaseOrder"))
        if number not in selected:
            raise rest.FeedError("sap_purchase_order_scope_mismatch",502)
        row=[rest._key(p,"sap_event","po:"+number),rest._key(p,"sap_po_case",number),
             "sap.purchase_order.created_day",rest._iso(at),""]
        return (at,number),[row],0,0
    year=key(item.get(keys[0]),YEAR);document=key(item.get(keys[1]),DOC).zfill(10)
    related=item.get(nav);children=collection(related)
    if len(children)>1000 or related.get("__next") is not None:
        raise rest.FeedError("sap_expanded_reference_collection_incomplete",502)
    if "__count" in related and str(related["__count"])!=str(len(children)):
        raise rest.FeedError("sap_expanded_reference_count_mismatch",502)
    rows=[];seen=set();unselected=0
    line_key="MaterialDocumentItem" if kind=="material_movements" else "SupplierInvoiceItem"
    for child in children:
        if not isinstance(child,dict) or child.get(keys[0])!=year or key(child.get(keys[1]),DOC).zfill(10)!=document:
            raise rest.FeedError("sap_reference_parent_mismatch",502)
        line=key(child.get(line_key),ITEM).zfill(6)
        if line in seen:
            raise rest.FeedError("sap_duplicate_reference_line",502)
        seen.add(line)
        number=child.get("PurchaseOrder")
        if number in ("",None):
            unselected+=1;continue
        number=po(number)
        if number not in selected:
            unselected+=1;continue
        # The PO item is checked, but the case is deliberately PO header
        # level. No invoice or material-document relationships are inferred
        # beyond the explicit native purchase-order reference.
        key(child.get("PurchaseOrderItem"),ITEM)
        activity=("sap.material_movement."+key(child.get("GoodsMovementType"),MOVE)+".posting_day"
                  if kind=="material_movements" else "sap.supplier_invoice.po_reference_posting_day")
        event=kind+":"+year+":"+document+":"+line
        rows.append([rest._key(p,"sap_event",event),rest._key(p,"sap_po_case",number),
                     activity,rest._iso(at),""])
    return (at,year,document),rows,len(children),unselected


class Reader:
    def __init__(self,p,client):
        self.p=p;self.src=source(p["config"]);self.client=client
        self.deadline=time.monotonic()+rest.MAX_SECONDS;self.bytes=0;self.pages=0

    def get(self,url,params=None,metadata=False):
        if self.pages>=rest.MAX_PAGES:
            raise rest.FeedError("provider_page_budget_exceeded")
        self.pages+=1
        secret=rest._check(self.p)
        left=self.deadline-time.monotonic()
        if left<=0:
            raise rest.FeedError("read_time_budget_exceeded")
        try:
            with self.client.stream("GET",url,params=params,headers={"Authorization":secret,
                     "Accept":"application/xml" if metadata else "application/json"},timeout=min(10,left)) as response:
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
                    raw.extend(chunk)
                    if len(raw)>rest.MAX_PAGE_BYTES or self.bytes+len(raw)>rest.MAX_TOTAL_BYTES or time.monotonic()>self.deadline:
                        raise rest.FeedError("provider_response_budget_exceeded")
                self.bytes+=len(raw)
        except httpx.HTTPError:
            raise rest.FeedError("provider_transport_failure",502) from None
        return bytes(raw)

    def verify_schema(self,kind):
        service=SOURCES[kind][0]
        raw=self.get(self.src["service_root"]+"/"+service+"/$metadata",
                     {"sap-client":self.src["sap_client"]},metadata=True)
        if sha256(raw).hexdigest()!=self.src["metadata_sha256"][kind]:
            raise rest.FeedError("sap_metadata_changed_requires_review")

    def stream(self,kind,since,until,budget):
        self.verify_schema(kind)
        service,entity,*_=SOURCES[kind]
        root=self.src["service_root"]+"/"+service+"/"+entity
        original=query(self.p["config"],kind,since,until)
        # $top is a total result limit in OData, not a page-size guarantee.
        # Omit it and let server-driven pagination return complete results;
        # use configured page_size as a strict local acceptance bound.
        original.pop("$top")
        original["sap-client"]=self.src["sap_client"]
        seen=set();rows=[];headers=children=unselected=0;previous=None;total=None;params=original;url=root
        while True:
            body=rest._strict_json(self.get(url,params))
            if not isinstance(body,dict) or not isinstance(body.get("d"),dict):
                raise rest.FeedError("invalid_sap_odata_response",502)
            data=body["d"];records=collection(data)
            count=data.get("__count")
            if not isinstance(count,str) or not re.fullmatch(r"[0-9]{1,12}",count):
                raise rest.FeedError("sap_inline_count_unavailable",502)
            if total is None:
                total=int(count)
            if int(count)!=total or total>rest.MAX_ROWS or len(records)>self.p["config"]["page_size"]:
                raise rest.FeedError("sap_count_or_page_budget_exceeded",502)
            headers+=len(records)
            for record in records:
                order,batch,n,skipped=project(self.p,kind,record,since,until)
                if previous is not None and order<=previous:
                    raise rest.FeedError("sap_duplicate_or_unordered_header",502)
                previous=order;children+=n;unselected+=skipped;rows.extend(batch)
                if len(rows)>budget or children>rest.MAX_ROWS:
                    raise rest.FeedError("provider_row_budget_exceeded")
            cursor=data.get("__next")
            if cursor is None:
                if headers!=total:
                    raise rest.FeedError("sap_incomplete_header_traversal",502)
                return rows,{"headers":headers,"references":children,"unselected_references":unselected,"events":len(rows)}
            if not records or headers>=total or not isinstance(cursor,str) or len(cursor)>16000:
                raise rest.FeedError("invalid_sap_cursor",502)
            candidate=urljoin(root,cursor);parts=urlsplit(candidate);base=urlsplit(root)
            if (parts.scheme,parts.netloc,parts.path)!=(base.scheme,base.netloc,base.path) or parts.fragment or parts.username or parts.password:
                raise rest.FeedError("invalid_sap_cursor",502)
            pairs=parse_qsl(parts.query,keep_blank_values=True,max_num_fields=20)
            next_params=dict(pairs)
            if len(pairs)!=len(next_params) or set(next_params)-set(original)-{"$skiptoken","$skip"}:
                raise rest.FeedError("invalid_sap_cursor",502)
            # Never let a provider continuation broaden the approved scope.
            if any(next_params.get(k)!=v for k,v in original.items() if k in next_params):
                raise rest.FeedError("sap_cursor_scope_changed",502)
            paging={k:v for k,v in next_params.items() if k in ("$skiptoken","$skip")}
            if len(paging)!=1 or not all(0<len(v)<=4096 and not any(ord(c)<32 or ord(c)==127 for c in v) for v in paging.values()):
                raise rest.FeedError("invalid_sap_cursor",502)
            pin=tuple(paging.items())
            if pin in seen:
                raise rest.FeedError("invalid_sap_cursor",502)
            seen.add(pin);url=root;params={**original,**paging}


def extract(p,since,until,client_factory=httpx.Client):
    rows=[];counts={};seen=set();references=headers=0
    with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
        reader=Reader(p,client)
        for kind in p["config"]["streams"]:
            batch,receipt=reader.stream(kind,since,until,rest.MAX_ROWS-len(rows))
            if any(r[0] in seen for r in batch) or len({r[0] for r in batch})!=len(batch):
                raise rest.FeedError("sap_duplicate_event_identity",502)
            seen.update(r[0] for r in batch);rows.extend(batch);counts[kind]=receipt
            references+=receipt["references"];headers+=receipt["headers"]
            if references+headers>rest.MAX_ROWS:
                raise rest.FeedError("provider_row_budget_exceeded")
        # No metadata changes during an accepted multi-service read.
        for kind in p["config"]["streams"]:
            reader.verify_schema(kind)
        return rows,{"pages":reader.pages,"records_received":headers+references,
                     "duplicate_records_in_window":0,"stream_counts":counts,"date_precision":"day",
                     "metadata_pins_verified":True,"provider_completeness_verified":False,
                     "payment_events_inferred":False,"source_principal_independently_verified":False}
