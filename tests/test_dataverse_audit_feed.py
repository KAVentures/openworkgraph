"""Dataverse native request and transactional retained-feed regressions."""
from datetime import timedelta
import json
from urllib.parse import parse_qs

import httpx
import pytest

from server import dataverse_audit_feed as dv
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import local_scheduler as scheduler
from server import enterprise_connector_operations as ops
from server.enterprise_connector_crosswalk import native_case, InputRow
from gateway.connector_jobs import Create as JobCreate, profile_digest

ORIGIN="https://customer.crm4.dynamics.com"
ENV="OWG_DATAVERSE_SECRET_TEST"
SECRET="synthetic-client-secret"
TOKEN="synthetic-access-token"


def gid(n):
    return f"00000000-0000-0000-0000-{n:012x}"


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(ws,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setenv(ENV,SECRET)
    data={"primary":{"origin":ORIGIN,"tenant_id":gid(1),"client_id":gid(2),
          "organization_id":gid(3),"application_user_id":gid(4),"credential_env":ENV}}
    monkeypatch.setenv(dv.CATALOG,json.dumps(data))
    return data


def config(**overrides):
    since=(rest._now()-timedelta(days=3)).replace(microsecond=0)
    return {"name":"Dataverse fixture","source_alias":"primary",
            "source_sha256":dv.catalog_public()["sources"][0]["source_sha256"],
            "selections":[{"table":t,"record_ids":[gid(100+i)]} for i,t in enumerate(dv.TABLES)],
            "operations":[1,2,3],"initial_since":rest._iso(since),
            "window_hours":24,"overlap_minutes":60,"ingestion_lag_minutes":15,"page_size":2,
            "confirm_configuration":True,"confirm_audit_retention":True,
            "confirm_application_identity":True,"confirm_unrestricted_records":False,**overrides}


def item(p,table,n=None,*,at=None):
    idx=dv.TABLES.index(table)
    start=rest._stamp(p["config"]["initial_since"])
    return {"auditid":gid(1000+idx if n is None else n),"_objectid_value":gid(100+idx),
            "objecttypecode":table,"createdon":rest._iso(at or start+timedelta(hours=23,minutes=30)),
            "operation":2,"action":2,"_userid_value":gid(9),"changedata":"private-field-value"}


def page(rows,next_link=None):
    d={"value":rows}
    if next_link is not None:d["@odata.nextLink"]=next_link
    return httpx.Response(200,json=d)


def run(p,fetch,identity=None,auth=None):
    def handle(req):
        if req.url.host=="login.microsoftonline.com":
            assert req.method=="POST"
            assert req.url.path=="/"+gid(1)+"/oauth2/v2.0/token"
            assert parse_qs(req.content.decode())=={"grant_type":["client_credentials"],
                "client_id":[gid(2)],"client_secret":[SECRET],"scope":[ORIGIN+"/.default"]}
            return auth() if auth else httpx.Response(200,json={"access_token":TOKEN,"token_type":"Bearer","expires_in":3600})
        assert req.url.host=="customer.crm4.dynamics.com"
        assert req.method=="GET" and req.headers["Authorization"]=="Bearer "+TOKEN
        if req.url.path.endswith("/WhoAmI"):
            return identity() if identity else httpx.Response(200,json={"OrganizationId":gid(3),"UserId":gid(4)})
        assert req.url.path=="/api/data/v9.2/audits"
        assert req.url.params["$select"]==dv.FIELDS
        assert "changedata" not in req.url.params["$select"]
        assert req.url.params["$orderby"]=="createdon asc,auditid asc"
        return fetch(req)
    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handle),**kwargs)
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,client_factory=factory)


def table_for(req):
    return req.url.params["$filter"].split("objecttypecode eq '")[1].split("'")[0]


def test_all_ten_tables_retention_replay_and_crosswalk():
    p=dv.create(config())
    def fetch(req):
        table=table_for(req)
        assert "_objectid_value eq "+gid(100+dv.TABLES.index(table)) in req.url.params["$filter"]
        return page([item(p,table)])
    first=run(p,fetch)
    assert first["events_added"]==10
    assert first["last_result"]["table_counts"]=={t:1 for t in dv.TABLES}
    assert first["last_result"]["application_identity_readback_verified"]
    second=run(first,fetch)
    assert second["events_added"]==0 and second["duplicates_skipped"]==10
    assert ws.list_logs()["logs"][0]["events"]==10
    with ws._LOCK:
        db=rest._connect()
        try:
            private=rest._get(db,p["profile_id"])
            case=native_case(private,InputRow(native_case_id=gid(100),native_type="account",business_key="fixture"))
            assert case==rest._key(private,"dataverse_case","account:"+gid(100))
            raw=str(db.execute("SELECT * FROM events").fetchall())+str(db.execute("SELECT * FROM objects").fetchall())
        finally:
            db.close()
    for secret in (SECRET,TOKEN,gid(100),gid(9),"private-field-value"):
        assert secret not in raw


def test_paging_reconstructs_exact_scope():
    p=dv.create(config(selections=[{"table":"account","record_ids":[gid(100)]}]))
    def fetch(req):
        if req.url.params.get("$skiptoken"):
            return page([item(p,"account",1001)])
        return page([item(p,"account")],ORIGIN+dv.PATH+"audits?%24skiptoken=opaque")
    assert run(p,fetch)["events_added"]==2


@pytest.mark.parametrize("cursor",["https://evil.example/api/data/v9.2/audits?$skiptoken=x",
    ORIGIN+dv.PATH+"contacts?$skiptoken=x",ORIGIN+dv.PATH+"audits?$skiptoken=x&$filter=evil",
    ORIGIN+dv.PATH+"audits?$skiptoken=x&$skiptoken=y"])
def test_cursor_scope_rejected(cursor):
    p=dv.create(config(selections=[{"table":"account","record_ids":[gid(100)]}]))
    with pytest.raises(rest.FeedError):
        run(p,lambda _:page([item(p,"account")],cursor))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_identity_change_after_read_rolls_back():
    p=dv.create(config(selections=[{"table":"account","record_ids":[gid(100)]}]))
    ids=iter([gid(3),gid(99)])
    with pytest.raises(rest.FeedError,match="dataverse_application_identity_mismatch"):
        run(p,lambda _:page([item(p,"account")]),
            identity=lambda:httpx.Response(200,json={"OrganizationId":next(ids),"UserId":gid(4)}))
    assert ws.list_logs()["logs"]==[]


def test_failed_second_table_no_partial_commit():
    p=dv.create(config(selections=[{"table":"account","record_ids":[gid(100)]},{"table":"contact","record_ids":[gid(101)]}]))
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        run(p,lambda req:page([item(p,"account")]) if table_for(req)=="account" else httpx.Response(503))
    assert ws.list_logs()["logs"]==[]


@pytest.mark.parametrize("changes",[
    {"objecttypecode":"contact"},{"_objectid_value":gid(999)},{"operation":4},
    {"operation":True},{"action":"2"},{"createdon":"2020-01-01T00:00:00Z"},
])
def test_outside_projection_rejected(changes):
    p=dv.create(config(selections=[{"table":"account","record_ids":[gid(100)]}]))
    with pytest.raises(rest.FeedError):
        run(p,lambda _:page([{**item(p,"account"),**changes}]))
    assert ws.list_logs()["logs"]==[]


def test_duplicate_audit_identity_cross_table():
    p=dv.create(config(selections=[{"table":"account","record_ids":[gid(100)]},{"table":"contact","record_ids":[gid(101)]}]))
    with pytest.raises(rest.FeedError,match="cross_table_audit_identity_conflict"):
        run(p,lambda req:page([item(p,table_for(req),1000)]))


def test_auth_error_and_rate_limit():
    p=dv.create(config())
    with pytest.raises(rest.FeedError,match="dataverse_application_auth_failed"):
        run(p,lambda _:pytest.fail("no source read"),auth=lambda:httpx.Response(401))
    with pytest.raises(rest.FeedError,match="provider_rate_limited"):
        run(p,lambda _:httpx.Response(429,headers={"Retry-After":"120"}))


@pytest.mark.parametrize("changes",[
    {"confirm_configuration":False},{"confirm_application_identity":False},{"confirm_audit_retention":False},
    {"selections":[{"table":"account","record_ids":[]}]},
    {"operations":[4]},{"operations":[6]},{"operations":[1,1]},
    {"source_sha256":"a"*64},
])
def test_scope_and_consent(changes):
    with pytest.raises(rest.FeedError):
        dv.create(config(**changes))


def test_catalog_drift_and_secret_drift(setup,monkeypatch):
    p=dv.create(config())
    setup["primary"]["application_user_id"]=gid(99)
    monkeypatch.setenv(dv.CATALOG,json.dumps(setup))
    with pytest.raises(rest.FeedError,match="dataverse_source_definition_changed"):
        run(p,lambda _:pytest.fail("no read"))
    setup["primary"]["application_user_id"]=gid(4)
    monkeypatch.setenv(dv.CATALOG,json.dumps(setup))
    monkeypatch.setenv(ENV,"changed-secret")
    with pytest.raises(rest.FeedError,match="credential_changed_reconfigure_required"):
        run(p,lambda _:pytest.fail("no read"))


def test_gateway_and_passive_inventory():
    p=dv.create(config())
    row=next(x for x in ops.inventory()["profiles"] if x["target_id"]==p["profile_id"])
    assert row["provider"]=="dataverse" and row["provider_live_health"]=="unknown"
    for secret in (SECRET,ORIGIN,ENV,gid(3)):
        assert secret not in json.dumps(row)
    job=JobCreate(organization_id="customer",name="Dataverse",provider="dataverse",
        profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=profile_digest(p),
        interval_minutes=60,confirm_dispatch=True,confirm_counts_only_receipts=True)
    assert job.provider=="dataverse"
    scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=60,
        confirm_scheduled_runs=True,confirm_rest_read_retention=True,expected_rest_profile_revision=p["revision"])
