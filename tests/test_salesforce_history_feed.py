"""Native Salesforce history source tests using real retained event-log transactions."""
from datetime import timedelta
import json

import httpx
import pytest

from server import connector_oauth as oauth
from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler
from server import salesforce_history_feed as sf
from gateway.connector_jobs import Create as JobCreate,profile_digest

CONNECTION="conn_"+"a"*16
HOST="customer.my.salesforce.com"
ORG="00D000000000001"
USER="005000000000001"
TOKEN="private-fixture-token"


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    grants=[{"connection_id":CONNECTION,"provider":"salesforce","host":HOST,
             "scopes":["api","refresh_token","id"],"has_refresh_token":True,"reauthorization_required":False}]
    monkeypatch.setattr(oauth,"status",lambda:{"connections":grants})
    monkeypatch.setattr(oauth,"api_host",lambda _:HOST)
    monkeypatch.setattr(oauth,"bearer_for",lambda *_:TOKEN)
    return grants


def record_id(obj,n=1):
    return sf.OBJECTS[obj][1]+f"{n:012d}"


def config(**overrides):
    since=(rest._now()-timedelta(days=2)).replace(microsecond=0)
    return {"name":"CRM history fixture","connection_id":CONNECTION,"instance_host":HOST,
            "organization_id":ORG,"user_id":USER,
            "selections":[{"object":obj,"fields":["Status","Owner"],"record_ids":[record_id(obj)]}
                          for obj in sf.OBJECTS],
            "initial_since":rest._iso(since),"window_hours":24,"overlap_minutes":60,
            "ingestion_lag_minutes":15,"page_size":200,"confirm_configuration":True,
            "confirm_field_labels":True,"confirm_identity_metadata":True,
            "confirm_unrestricted_records":False,**overrides}


def item(p,obj,n=1,*,at=None,field="Status",case=None):
    start=rest._stamp(p["config"]["initial_since"])
    return {"attributes":{"type":obj},"Id":"017"+f"{n:012d}",
            sf.OBJECTS[obj][0]:case or record_id(obj),"CreatedById":USER,
            "CreatedDate":rest._iso(at or start+timedelta(hours=1)),"Field":field,
            "OldValue":"never-retain-old-value","NewValue":"never-retain-new-value"}


def page(records,*,total=None,done=True,cursor=None):
    body={"records":records,"totalSize":len(records) if total is None else total,"done":done}
    if cursor is not None:
        body["nextRecordsUrl"]=cursor
    return httpx.Response(200,json=body)


def identity(**extra):
    return httpx.Response(200,json={"organization_id":ORG,"user_id":USER,"email":"private@example.com",**extra})


def route(handler,identity_response=identity):
    def fetch(request):
        assert request.url.host==HOST
        assert request.headers["Authorization"]=="Bearer "+TOKEN
        if request.url.path.startswith("/id/"):
            assert request.url.path=="/id/"+ORG+"/"+USER
            return identity_response()
        return handler(request)
    return fetch


def factory(handler):
    def create(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
    return create


def run(p,handler):
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,
                    client_factory=factory(handler))


def object_from(request):
    return request.url.params["q"].split(" FROM ",1)[1].split(" WHERE ",1)[0]


def test_five_object_window_atomic_and_replayed_with_distinct_case_namespaces():
    p=sf.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    calls=[]
    def fetch(request):
        calls.append(request)
        obj=object_from(request)
        soql=request.url.params["q"]
        assert "OldValue" not in soql and "NewValue" not in soql
        assert "Field IN ('Owner','Status')" in soql
        assert sf.OBJECTS[obj][0]+" IN ('"+record_id(obj)+"')" in soql
        assert "CreatedDate >= " in soql and "CreatedDate < " in soql
        return page([item(p,obj,at=start+timedelta(hours=23,minutes=30))])
    first=run(p,route(fetch))
    assert len(calls)==5 and first["events_added"]==5
    assert first["last_result"]["object_counts"]=={obj:1 for obj in sf.OBJECTS}
    assert first["last_result"]["principal_readback_verified"]
    assert workspace.list_logs()["logs"][0]["events"]==5
    second=run(first,route(fetch))
    assert second["events_added"]==0 and second["duplicates_skipped"]==5
    assert second["log_id"]==first["log_id"]
    with workspace._LOCK:
        db=workspace._connect()
        try:
            raw=str(db.execute("SELECT * FROM events").fetchall())+str(db.execute("SELECT * FROM objects").fetchall())
        finally:
            db.close()
    for secret in (USER,TOKEN,"never-retain-old-value","never-retain-new-value",record_id("AccountHistory")):
        assert secret not in raw
    for obj in sf.OBJECTS:
        assert "salesforce."+obj+".Status" in raw


def test_native_cursor_exact_total_and_no_partial_multi_object_commit():
    p=sf.create(config())
    calls=[]
    def fetch(request):
        calls.append(request)
        if request.url.path.endswith("/query/abc-200"):
            return page([item(p,"AccountHistory",2)],total=2)
        obj=object_from(request)
        if obj=="AccountHistory":
            return page([item(p,obj)],total=2,done=False,cursor="/services/data/v60.0/query/abc-200")
        return httpx.Response(503)
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        run(p,route(fetch))
    assert len(calls)==3
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


@pytest.mark.parametrize("cursor",["https://evil.example/steal","/services/data/v60.0/query/../x",
                                    "/services/data/v61.0/query/abc","/services/data/v60.0/query/abc?token=x"])
def test_unsafe_cursor_refused(cursor):
    p=sf.create(config())
    with pytest.raises(rest.FeedError,match="invalid_salesforce_query_cursor"):
        run(p,route(lambda _:page([item(p,"AccountHistory")],total=2,done=False,cursor=cursor)))


@pytest.mark.parametrize("make,code",[
    (lambda p:page([item(p,"ContactHistory")]),"salesforce_history_object_mismatch"),
    (lambda p:page([item(p,"AccountHistory",field="Unapproved")]),"salesforce_filter_mismatch"),
    (lambda p:page([item(p,"AccountHistory",case=record_id("AccountHistory",2))]),"salesforce_filter_mismatch"),
    (lambda p:page([item(p,"AccountHistory")],total=2),"incomplete_salesforce_query"),
    (lambda p:page([item(p,"AccountHistory"),item(p,"AccountHistory")]),"duplicate_salesforce_query_identity"),
    (lambda p:page([],total=10001),"salesforce_query_count_changed_or_budget_exceeded"),
])
def test_provider_contract_fail_closed(make,code):
    p=sf.create(config())
    with pytest.raises(rest.FeedError,match=code):
        run(p,route(lambda _:make(p)))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


def test_identity_mismatch_before_read_and_after_read():
    p=sf.create(config())
    with pytest.raises(rest.FeedError,match="salesforce_principal_mismatch"):
        run(p,route(lambda _:pytest.fail("query must not run"),lambda:identity(user_id="005000000000002")))
    replies=iter([identity(),identity(organization_id="00D000000000002")])
    with pytest.raises(rest.FeedError,match="salesforce_principal_mismatch"):
        run(p,route(lambda req:page([item(p,object_from(req))]),lambda:next(replies)))
    assert workspace.list_logs()["logs"]==[]


def test_revoked_grant_and_api_host_drift_prevent_source_read(setup,monkeypatch):
    p=sf.create(config())
    monkeypatch.setattr(oauth,"api_host",lambda _:"other.my.salesforce.com")
    with pytest.raises(rest.FeedError,match="salesforce_grant_unavailable"):
        run(p,lambda _:pytest.fail("no source read"))
    monkeypatch.setattr(oauth,"api_host",lambda _:HOST)
    setup.clear()
    assert not rest.inventory()["profiles"][0]["credential_binding_available"]


def test_403_request_limit_enters_cooldown():
    p=sf.create(config())
    with pytest.raises(rest.FeedError,match="provider_rate_limited"):
        run(p,route(lambda _:httpx.Response(403,json=[{"errorCode":"REQUEST_LIMIT_EXCEEDED","message":"private"}])))
    assert rest.inventory()["profiles"][0]["retry_not_before"]>rest.time.time()
    with pytest.raises(rest.FeedError,match="provider_retry_cooldown"):
        run(p,lambda _:pytest.fail("cooldown must prevent HTTP"))


def test_15_and_18_character_ids_are_canonical_and_checksummed():
    assert sf.sfid(ORG+"EAA","00D")==ORG
    with pytest.raises(rest.FeedError,match="checksum"):
        sf.sfid(ORG+"AAA","00D")
    with pytest.raises(rest.FeedError):
        sf.sfid(USER,"00D")


@pytest.mark.parametrize("overrides",[
    {"confirm_configuration":False},{"confirm_field_labels":False},{"confirm_identity_metadata":False},
    {"instance_host":"evil.example"},{"window_hours":1,"overlap_minutes":60},
    {"selections":[{"object":"AccountHistory","fields":["Name' OR Id!='"],"record_ids":[]}]},
    {"selections":[{"object":"AccountHistory","fields":["Name"],"record_ids":[]}]},
    {"selections":[{"object":"AccountHistory","fields":["Name"],"record_ids":[USER]}]},
])
def test_profile_requires_explicit_bounded_scope(overrides):
    with pytest.raises(rest.FeedError):
        sf.create(config(**overrides))


def test_unrestricted_records_requires_opt_in_and_gateway_job_support():
    p=sf.create(config(selections=[{"object":"AccountHistory","fields":["Name"],"record_ids":[]}],
                       confirm_unrestricted_records=True))
    start=rest._stamp(p["config"]["initial_since"])
    assert "AccountId IN" not in sf.query(p["config"]["selections"][0],start,start+timedelta(hours=1))
    job=JobCreate(organization_id="acme",name="Salesforce",provider="salesforce",
        profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=profile_digest(p),
        interval_minutes=60,confirm_dispatch=True,confirm_counts_only_receipts=True)
    assert job.provider=="salesforce"
    sched=scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=60,
        confirm_scheduled_runs=True,confirm_rest_read_retention=True,expected_rest_profile_revision=p["revision"])
    assert sched["rest_profile_revision"]==p["revision"]


def test_passive_inventory_does_not_refresh_or_disclose_scope(monkeypatch):
    p=sf.create(config())
    monkeypatch.setattr(oauth,"bearer_for",lambda *_:pytest.fail("inventory must not refresh"))
    r=ops.inventory()
    row=next(x for x in r["profiles"] if x["target_id"]==p["profile_id"])
    assert row["provider"]=="salesforce" and row["provider_live_health"]=="unknown"
    for secret in (ORG,USER,HOST,CONNECTION,TOKEN):
        assert secret not in json.dumps(r)
