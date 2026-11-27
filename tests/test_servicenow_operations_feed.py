"""Native ServiceNow Table API contracts and real retained-log transactions."""
from datetime import timedelta
import json

import httpx
import pytest

from server import connector_oauth as oauth
from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler
from server import servicenow_operations_feed as sn
from gateway.connector_jobs import Create as JobCreate,profile_digest

CONNECTION="conn_"+"a"*16
HOST="customer.service-now.com"
TOKEN="private-fixture-token"


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    grants=[{"connection_id":CONNECTION,"provider":"servicenow","host":HOST,
             "scopes":["useraccount"],"has_refresh_token":True,"reauthorization_required":False}]
    monkeypatch.setattr(oauth,"status",lambda:{"connections":grants})
    monkeypatch.setattr(oauth,"api_host",lambda _:HOST)
    monkeypatch.setattr(oauth,"bearer_for",lambda *_:TOKEN)
    return grants


def sid(n):
    return f"{n:032x}"


def config(**overrides):
    since=(rest._now()-timedelta(days=2)).replace(microsecond=0)
    return {"name":"ServiceNow fixture","connection_id":CONNECTION,"instance_host":HOST,
            "selections":[{"table":table,"fields":["state","assignment_group"],
                           "record_ids":[sid(i+1)],"include_created":False}
                          for i,table in enumerate(sn.TABLES)],
            "initial_since":rest._iso(since),"window_hours":24,"overlap_minutes":60,
            "ingestion_lag_minutes":15,"page_size":2,"verify_repeat":True,
            "confirm_configuration":True,"confirm_utc_account":True,"confirm_audit_retention":True,
            "confirm_creation_retention":False,"confirm_unrestricted_records":False,**overrides}


def item(p,table,n=101,*,at=None,field="state",case=None):
    start=rest._stamp(p["config"]["initial_since"])
    return {"sys_id":sid(n),"documentkey":case or sid(sn.TABLES.index(table)+1),
            "tablename":table,"fieldname":field,
            "sys_created_on":(at or start+timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "oldvalue":"private-old","newvalue":"private-new","user":"private-user"}


def created(p,table,*,at=None):
    return {"sys_id":sid(sn.TABLES.index(table)+1),
            "sys_created_on":(at or rest._stamp(p["config"]["initial_since"])+timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
            "short_description":"private-description"}


def page(records,*,total=None,header=True):
    return httpx.Response(200,json={"result":records},
                          headers={"X-Total-Count":str(len(records) if total is None else total)} if header else {})


def factory(handler):
    def make(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
    return make


def run(p,handler):
    def fetch(request):
        assert request.url.host==HOST
        assert request.headers["Authorization"]=="Bearer "+TOKEN
        assert request.method=="GET"
        assert request.url.params["sysparm_display_value"]=="false"
        assert request.url.params["sysparm_exclude_reference_link"]=="true"
        assert request.url.params["sysparm_no_count"]=="false"
        assert "sysparm_query_no_domain" not in request.url.params
        return handler(request)
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,
                    client_factory=factory(fetch))


def table_from(request):
    return request.url.params["sysparm_query"].split("tablename=",1)[1].split("^",1)[0]


def selection(table="incident",**changes):
    return {"table":table,"fields":["state"],"record_ids":[sid(sn.TABLES.index(table)+1)],
            "include_created":False,**changes}


def test_all_eight_tables_repeat_and_overlap_retention():
    p=sn.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    calls=[]
    def fetch(request):
        calls.append(request)
        table=table_from(request)
        assert request.url.path=="/api/now/table/sys_audit"
        assert request.url.params["sysparm_fields"]==",".join(sn.AUDIT_FIELDS)
        q=request.url.params["sysparm_query"]
        assert "fieldnameINassignment_group,state" in q
        assert "documentkeyIN"+sid(sn.TABLES.index(table)+1) in q
        assert q.endswith("ORDERBYsys_created_on^ORDERBYsys_id")
        return page([item(p,table,at=start+timedelta(hours=23,minutes=30))])
    first=run(p,fetch)
    assert len(calls)==16 and first["events_added"]==8
    assert first["last_result"]["stream_counts"]=={table+":audit":1 for table in sn.TABLES}
    assert first["last_result"]["repeat_read_matched"] is True
    second=run(first,fetch)
    assert second["events_added"]==0 and second["duplicates_skipped"]==8
    assert workspace.list_logs()["logs"][0]["events"]==8
    with workspace._LOCK:
        db=workspace._connect()
        try:
            raw=str(db.execute("SELECT * FROM events").fetchall())+str(db.execute("SELECT * FROM objects").fetchall())
        finally:
            db.close()
    for secret in (sid(101),sid(1),TOKEN,"private-old","private-new","private-user"):
        assert secret not in raw
    for table in sn.TABLES:
        assert "servicenow."+table+".field.state" in raw


def test_creation_and_audit_share_typed_case():
    p=sn.create(config(selections=[selection(include_created=True)],confirm_creation_retention=True))
    def fetch(request):
        if request.url.path.endswith("/incident"):
            assert request.url.params["sysparm_fields"]=="sys_id,sys_created_on"
            assert "sys_idIN"+sid(1) in request.url.params["sysparm_query"]
            return page([created(p,"incident")])
        return page([item(p,"incident")])
    result=run(p,fetch)
    assert result["events_added"]==2
    assert result["last_result"]["stream_counts"]=={"incident:audit":1,"incident:created":1}
    with workspace._LOCK:
        db=workspace._connect()
        try:
            raw=str(db.execute("SELECT * FROM events").fetchall())
            assert db.execute("SELECT count(*) FROM objects").fetchone()[0]==1
        finally:
            db.close()
    assert "servicenow.incident.record_created" in raw
    assert "private-description" not in raw


def test_creation_only_without_audit_permission():
    p=sn.create(config(selections=[selection(fields=[],include_created=True)],confirm_creation_retention=True,
                       confirm_audit_retention=False,verify_repeat=False))
    def fetch(request):
        assert request.url.path.endswith("/incident")
        return page([created(p,"incident")])
    assert run(p,fetch)["events_added"]==1


def test_count_checked_paging_offsets_and_repeat():
    p=sn.create(config(selections=[selection()]))
    calls=[]
    def fetch(request):
        offset=int(request.url.params["sysparm_offset"]);calls.append(offset)
        return page([item(p,"incident",103)] if offset==2 else
                    [item(p,"incident",101),item(p,"incident",102)],total=3)
    result=run(p,fetch)
    assert result["events_added"]==3 and calls==[0,2,0,2]


@pytest.mark.parametrize("response,code",[
    (lambda p:page([item(p,"incident")],total=2),"servicenow_page_count_mismatch_or_acl_gap"),
    (lambda p:page([],header=False),"servicenow_total_count_unavailable"),
    (lambda p:page([item(p,"problem")]),"servicenow_record_filter_mismatch"),
    (lambda p:page([item(p,"incident",field="unapproved")]),"servicenow_audit_filter_mismatch"),
    (lambda p:page([item(p,"incident",case=sid(999))]),"servicenow_record_filter_mismatch"),
    (lambda p:page([item(p,"incident",102),item(p,"incident",101)]),"servicenow_duplicate_or_unordered_page"),
    (lambda p:page([item(p,"incident"),item(p,"incident")]),"servicenow_duplicate_or_unordered_page"),
    (lambda p:page([],total=10001),"servicenow_count_changed_or_budget_exceeded"),
])
def test_bad_pages_never_advance_checkpoint(response,code):
    p=sn.create(config(selections=[selection()]))
    with pytest.raises(rest.FeedError,match=code):
        run(p,lambda _:response(p))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


def test_changed_count_between_pages_refused():
    p=sn.create(config(selections=[selection()]))
    def fetch(request):
        if request.url.params["sysparm_offset"]=="0":
            return page([item(p,"incident",101),item(p,"incident",102)],total=3)
        return page([item(p,"incident",103)],total=4)
    with pytest.raises(rest.FeedError,match="servicenow_count_changed"):
        run(p,fetch)


def test_same_count_different_repeat_payload_refused():
    p=sn.create(config(selections=[selection()]))
    replies=iter([page([item(p,"incident",101)]),page([item(p,"incident",102)])])
    with pytest.raises(rest.FeedError,match="servicenow_repeat_read_mismatch"):
        run(p,lambda _:next(replies))
    assert workspace.list_logs()["logs"]==[]


def test_failure_of_second_stream_never_commits_first():
    p=sn.create(config(selections=[selection(),selection("problem")]))
    def fetch(request):
        if table_from(request)=="incident":
            return page([item(p,"incident")])
        return httpx.Response(503)
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        run(p,fetch)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


def test_empty_stream_advances_without_fabricated_log():
    p=sn.create(config(selections=[selection()]))
    result=run(p,lambda _:page([]))
    assert result["checkpoint"] is not None and result["log_id"] is None
    assert result["events_added"]==0


def test_revoked_grant_and_host_drift_prevent_reads(setup,monkeypatch):
    p=sn.create(config(selections=[selection()]))
    monkeypatch.setattr(oauth,"api_host",lambda _:"other.service-now.com")
    with pytest.raises(rest.FeedError,match="servicenow_grant_unavailable"):
        run(p,lambda _:pytest.fail("no source read"))
    monkeypatch.setattr(oauth,"api_host",lambda _:HOST)
    setup.clear()
    assert not rest.inventory()["profiles"][0]["credential_binding_available"]


def test_revocation_during_page_prevents_commit(setup):
    p=sn.create(config(selections=[selection()],verify_repeat=False))
    def fetch(_):
        setup.clear()
        return page([item(p,"incident")])
    with pytest.raises(rest.FeedError,match="servicenow_grant_unavailable"):
        run(p,fetch)
    assert workspace.list_logs()["logs"]==[]


def test_rate_limit_and_redirect():
    p=sn.create(config(selections=[selection()]))
    with pytest.raises(rest.FeedError,match="provider_rate_limited"):
        run(p,lambda _:httpx.Response(429,headers={"Retry-After":"120"}))
    assert rest.inventory()["profiles"][0]["retry_not_before"]>rest.time.time()
    with pytest.raises(rest.FeedError,match="provider_retry_cooldown"):
        run(p,lambda _:pytest.fail("cooldown must prevent HTTP"))
    p=sn.create(config(selections=[selection()]))
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        run(p,lambda _:httpx.Response(302,headers={"Location":"https://evil.example/"}))


@pytest.mark.parametrize("overrides",[
    {"confirm_configuration":False},{"confirm_utc_account":False},{"confirm_audit_retention":False},
    {"instance_host":"evil.example"},{"window_hours":1,"overlap_minutes":60},
    {"selections":[selection(fields=["state^ORactive=true"])]},
    {"selections":[selection(record_ids=[])]},
    {"selections":[selection(record_ids=["bad"])]},
    {"selections":[selection(include_created=True)]},
    {"selections":[selection(fields=[])]},
    {"selections":[selection(),selection()]},
    {"selections":[selection(t,include_created=True) for t in sn.TABLES],"confirm_creation_retention":True},
])
def test_invalid_or_unapproved_scope_refused(overrides):
    with pytest.raises(rest.FeedError):
        sn.create(config(**overrides))


def test_unrestricted_explicit_scope_and_gateway_registration():
    p=sn.create(config(selections=[selection(record_ids=[])],confirm_unrestricted_records=True))
    start=rest._stamp(p["config"]["initial_since"])
    assert "documentkeyIN" not in sn.params(p["config"]["selections"][0],"audit",start,start+timedelta(hours=1),100,0)["sysparm_query"]
    job=JobCreate(organization_id="acme",name="ServiceNow",provider="servicenow",
        profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=profile_digest(p),
        interval_minutes=60,confirm_dispatch=True,confirm_counts_only_receipts=True)
    assert job.provider=="servicenow"
    scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=60,
        confirm_scheduled_runs=True,confirm_rest_read_retention=True,expected_rest_profile_revision=p["revision"])


def test_inventory_is_passive_and_sanitized(monkeypatch):
    p=sn.create(config(selections=[selection()]))
    monkeypatch.setattr(oauth,"bearer_for",lambda *_:pytest.fail("inventory must not refresh"))
    r=ops.inventory()
    row=next(x for x in r["profiles"] if x["target_id"]==p["profile_id"])
    assert row["provider"]=="servicenow" and row["provider_live_health"]=="unknown"
    for secret in (HOST,CONNECTION,TOKEN,sid(1)):
        assert secret not in json.dumps(r)
