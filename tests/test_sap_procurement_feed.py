"""SAP native procurement day evidence and real retained-log acceptance."""
from datetime import timedelta
from hashlib import sha256
import json

import httpx
import pytest

from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler
from server import sap_procurement_feed as sap
from gateway.connector_jobs import Create as JobCreate,profile_digest

ROOT="https://sap.customer.example/sap/opu/odata/sap"
ENV="OWG_SAP_P2P_TEST"
TOKEN="synthetic-private-token"
PO="4500001234"
OTHER="4500005678"
METADATA=b'<edmx:Edmx xmlns:edmx="urn:fixture"/>'
SHA=sha256(METADATA).hexdigest()


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setenv(ENV,TOKEN)
    cfg={"primary":{"service_root":ROOT,"sap_client":"100","auth_type":"bearer",
                    "credential_env":ENV,"metadata_sha256":{k:SHA for k in sap.SOURCES}}}
    monkeypatch.setenv(sap.CATALOG_ENV,json.dumps(cfg))
    return cfg


def config(**overrides):
    since=sap.midnight(rest._now())-timedelta(days=4)
    src=sap.catalog_public()["sources"][0]
    return {"name":"SAP procurement fixture","source_alias":"primary","source_sha256":src["source_sha256"],
            "purchase_orders":[PO],"streams":list(sap.SOURCES),"initial_since":rest._iso(since),
            "window_days":1,"overlap_days":1,"lag_days":1,"page_size":2,
            "confirm_configuration":True,"confirm_day_precision":True,"confirm_retention":True,
            "confirm_transient_unselected_references":True,**overrides}


def date(at):
    return "/Date("+str(int(at.timestamp())*1000)+")/"


def header(p,kind,*,at=None,n=1,selected=PO):
    at=at or rest._stamp(p["config"]["initial_since"])
    if kind=="purchase_orders":
        return {"PurchaseOrder":selected,"CreationDate":date(at),"Supplier":"private-supplier"}
    _,_,field,keys,nav,_=sap.SOURCES[kind]
    doc=f"{n:010d}";year="2026"
    line="MaterialDocumentItem" if kind=="material_movements" else "SupplierInvoiceItem"
    def child(i,number):
        return {keys[0]:year,keys[1]:doc,line:str(i),"PurchaseOrder":number,
                "PurchaseOrderItem":"10","GoodsMovementType":"101","private":"raw-unselected"}
    return {keys[0]:year,keys[1]:doc,field:date(at),nav:{"results":[child(1,selected),child(2,OTHER)]},
            "Amount":"private-amount"}


def page(records,*,count=None,cursor=None):
    d={"results":records,"__count":str(len(records) if count is None else count)}
    if cursor is not None:d["__next"]=cursor
    return httpx.Response(200,json={"d":d})


def kind_for(request):
    return next(k for k,v in sap.SOURCES.items() if "/"+v[0]+"/" in request.url.path)


def factory(handler):
    def make(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
    return make


def run(p,handler,metadata=lambda:METADATA):
    def fetch(request):
        assert request.url.host=="sap.customer.example"
        assert request.url.params["sap-client"]=="100"
        assert request.headers["Authorization"]=="Bearer "+TOKEN
        assert request.method=="GET"
        if request.url.path.endswith("/$metadata"):
            return httpx.Response(200,content=metadata())
        assert "$top" not in request.url.params
        assert request.url.params["$inlinecount"]=="allpages"
        assert "Supplier" not in request.url.params["$select"].split(",")
        return handler(request)
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,
                    client_factory=factory(fetch))


def test_three_native_streams_share_selected_purchase_order_case_and_replay():
    p=sap.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    calls=[]
    def fetch(request):
        kind=kind_for(request);calls.append(kind)
        if kind=="purchase_orders":
            assert "PurchaseOrder eq '"+PO+"'" in request.url.params["$filter"]
        else:
            assert request.url.params["$expand"]==sap.SOURCES[kind][4]
        return page([header(p,kind,at=start)])
    first=run(p,fetch)
    assert first["events_added"]==3 and len(calls)==3
    assert first["last_result"]["date_precision"]=="day"
    assert first["last_result"]["metadata_pins_verified"] is True
    assert first["last_result"]["stream_counts"]["material_movements"]["unselected_references"]==1
    assert first["last_result"]["stream_counts"]["supplier_invoices"]["unselected_references"]==1
    assert rest._stamp(first["checkpoint"])==start+timedelta(days=1)
    second=run(first,fetch)
    assert second["events_added"]==0 and second["duplicates_skipped"]==3
    assert workspace.list_logs()["logs"][0]["events"]==3
    with workspace._LOCK:
        db=workspace._connect()
        try:
            assert db.execute("SELECT count(*) FROM objects").fetchone()[0]==1
            raw=str(db.execute("SELECT * FROM events").fetchall())+str(db.execute("SELECT * FROM objects").fetchall())
        finally:
            db.close()
    for secret in (PO,OTHER,TOKEN,"private-supplier","raw-unselected","private-amount"):
        assert secret not in raw
    for label in ("sap.purchase_order.created_day","sap.material_movement.101.posting_day",
                  "sap.supplier_invoice.po_reference_posting_day"):
        assert label in raw


def test_native_cursor_reconstructs_original_scope():
    p=sap.create(config(streams=["purchase_orders"],purchase_orders=[PO,OTHER]))
    calls=[]
    def fetch(request):
        calls.append(request)
        assert PO in request.url.params["$filter"] and OTHER in request.url.params["$filter"]
        if request.url.params.get("$skiptoken"):
            return page([header(p,"purchase_orders",selected=OTHER)],count=2)
        return page([header(p,"purchase_orders")],count=2,cursor="?%24skiptoken=opaque")
    result=run(p,fetch)
    assert result["events_added"]==2 and len(calls)==2
    assert calls[1].url.params["$select"]==calls[0].url.params["$select"]


@pytest.mark.parametrize("cursor",["https://evil.example/steal","/different/entity?$skip=1",
                                    "?%24skip=1&sap-client=999","?%24skip=1&%24filter=anything",
                                    "?%24skip=1&%24skiptoken=x","?%24skip=1&%24skip=2"])
def test_unsafe_cursor_refused(cursor):
    p=sap.create(config(streams=["purchase_orders"]))
    with pytest.raises(rest.FeedError):
        run(p,lambda _:page([header(p,"purchase_orders")],count=2,cursor=cursor))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_schema_drift_after_data_blocks_atomic_commit():
    p=sap.create(config(streams=["purchase_orders"]))
    metadata=iter([METADATA,b"different-schema"])
    with pytest.raises(rest.FeedError,match="sap_metadata_changed_requires_review"):
        run(p,lambda _:page([header(p,"purchase_orders")]),metadata=lambda:next(metadata))
    assert workspace.list_logs()["logs"]==[]


def test_service_failure_never_commits_first_stream():
    p=sap.create(config())
    def fetch(req):
        kind=kind_for(req)
        return httpx.Response(503) if kind=="supplier_invoices" else page([header(p,kind)])
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        run(p,fetch)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


@pytest.mark.parametrize("mutation,code",[
    (lambda x:x.update(PostingDate="/Date(1001)/"),"invalid_sap_day_value"),
    (lambda x:x["to_MaterialDocumentItem"].update(__next="more"),"sap_expanded_reference_collection_incomplete"),
    (lambda x:x["to_MaterialDocumentItem"].update(__count="3"),"sap_expanded_reference_count_mismatch"),
    (lambda x:x["to_MaterialDocumentItem"]["results"][0].update(MaterialDocument="999"),"sap_reference_parent_mismatch"),
    (lambda x:x["to_MaterialDocumentItem"]["results"][1].update(MaterialDocumentItem="1"),"sap_duplicate_reference_line"),
])
def test_bad_header_or_expansion_refused(mutation,code):
    p=sap.create(config(streams=["material_movements"]))
    row=header(p,"material_movements");mutation(row)
    with pytest.raises(rest.FeedError,match=code):
        run(p,lambda _:page([row]))
    assert workspace.list_logs()["logs"]==[]


def test_empty_and_unselected_rows_are_not_fabricated():
    p=sap.create(config(streams=["supplier_invoices"]))
    result=run(p,lambda _:page([header(p,"supplier_invoices",selected=OTHER)]))
    assert result["events_added"]==0 and result["log_id"] is None
    assert result["last_result"]["stream_counts"]["supplier_invoices"]["unselected_references"]==2


def test_source_and_credential_pin_drift_no_reads(setup,monkeypatch):
    p=sap.create(config())
    setup["primary"]["sap_client"]="200"
    monkeypatch.setenv(sap.CATALOG_ENV,json.dumps(setup))
    with pytest.raises(rest.FeedError,match="sap_source_definition_changed"):
        run(p,lambda _:pytest.fail("no source read"))
    setup["primary"]["sap_client"]="100"
    monkeypatch.setenv(sap.CATALOG_ENV,json.dumps(setup))
    monkeypatch.setenv(ENV,"changed-private-token")
    with pytest.raises(rest.FeedError,match="credential_changed_reconfigure_required"):
        run(p,lambda _:pytest.fail("no source read"))


@pytest.mark.parametrize("overrides",[
    {"confirm_configuration":False},{"confirm_day_precision":False},{"confirm_retention":False},
    {"confirm_transient_unselected_references":False},{"purchase_orders":[]},{"purchase_orders":["1","0000000001"]},
    {"purchase_orders":["1' or 1=1"]},{"initial_since":"2026-01-01T10:00:00Z"},
    {"source_sha256":"a"*64},{"streams":["purchase_orders","purchase_orders"]},
])
def test_scope_and_precision_consent_required(overrides):
    with pytest.raises(rest.FeedError):
        sap.create(config(**overrides))


def test_passive_inventory_and_gateway_scheduler_support(monkeypatch):
    p=sap.create(config())
    r=ops.inventory()
    row=next(x for x in r["profiles"] if x["target_id"]==p["profile_id"])
    assert row["provider"]=="sap" and row["provider_live_health"]=="unknown"
    for secret in (TOKEN,ROOT,PO,ENV):
        assert secret not in json.dumps(r)
    job=JobCreate(organization_id="acme",name="SAP",provider="sap",
        profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=profile_digest(p),
        interval_minutes=60,confirm_dispatch=True,confirm_counts_only_receipts=True)
    assert job.provider=="sap"
    scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=60,
        confirm_scheduled_runs=True,confirm_rest_read_retention=True,expected_rest_profile_revision=p["revision"])
