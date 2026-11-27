"""HubSpot native batch reads and retained-feed acceptance."""
from datetime import timedelta
import json

import httpx
import pytest

from server import hubspot_history_feed as hs
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import local_scheduler as scheduler
from server import enterprise_connector_operations as ops
from server.enterprise_connector_crosswalk import native_case, InputRow
from gateway.connector_jobs import Create as JobCreate, profile_digest

ENV="OWG_HUBSPOT_TOKEN_TEST"
TOKEN="pat-na1-synthetic-fixture"
SCOPES=["oauth","crm.objects.contacts.read","crm.objects.companies.read","crm.objects.deals.read","tickets"]


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(ws,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setenv(ENV,TOKEN)
    cfg={"primary":{"hub_id":1001,"app_id":2001,"credential_env":ENV,"scopes":SCOPES}}
    monkeypatch.setenv(hs.CATALOG,json.dumps(cfg))
    return cfg


def config(**overrides):
    since=(rest._now()-timedelta(days=3)).replace(microsecond=0)
    return {"name":"HubSpot history fixture","source_alias":"primary",
            "source_sha256":hs.catalog_public()["sources"][0]["source_sha256"],
            "selections":[{"object_type":obj,"record_ids":[str(100+i)],
                           "properties":["status","owner"]} for i,obj in enumerate(hs.OBJECTS)],
            "initial_since":rest._iso(since),"window_hours":24,"overlap_minutes":60,
            "ingestion_lag_minutes":15,"batch_size":50,"include_created":True,
            "confirm_configuration":True,"confirm_transient_history_values":True,
            "confirm_metadata_retention":True,"confirm_ticket_scope":True,**overrides}


def record(p,obj,rid=None):
    start=rest._stamp(p["config"]["initial_since"])
    at=rest._iso(start+timedelta(hours=23,minutes=30))
    return {"id":rid or str(100+hs.OBJECTS.index(obj)),"archived":False,
            "createdAt":at,"updatedAt":at,"properties":{"hs_object_id":rid or str(100+hs.OBJECTS.index(obj))},
            "propertiesWithHistory":{"status":[{"timestamp":at,"value":"private-new-value",
                                  "sourceId":"private-source","sourceLabel":"private-label"}],
                                     "owner":[{"timestamp":rest._iso(start-timedelta(days=1)),
                                               "value":"private-old-value"}]}}


def batch(rows,**extra):
    return httpx.Response(200,json={"status":"COMPLETE","results":rows,**extra})


def run(p,fetch,identity=None):
    def handle(req):
        assert req.url.host=="api.hubapi.com" and req.method=="POST"
        assert req.headers["Authorization"]=="Bearer "+TOKEN
        body=json.loads(req.content)
        if req.url.path==hs.IDENTITY:
            assert body=={"tokenKey":TOKEN}
            return identity() if identity else httpx.Response(200,json={"hubId":1001,"appId":2001,"scopes":SCOPES})
        obj=req.url.path.split("/")[4]
        assert req.url.path=="/crm/v3/objects/"+obj+"/batch/read"
        assert body["properties"]==["hs_object_id"]
        assert set(body)=={"inputs","properties","propertiesWithHistory"}
        return fetch(obj,body)
    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handle),**kwargs)
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,client_factory=factory)


def test_four_objects_creation_history_replay_and_crosswalk():
    p=hs.create(config())
    def fetch(obj,body):
        assert body["propertiesWithHistory"]==["owner","status"]
        return batch([record(p,obj,r["id"]) for r in body["inputs"]])
    first=run(p,fetch)
    assert first["events_added"]==8
    assert first["last_result"]["history_versions_observed"]==8
    assert first["last_result"]["versions_outside_window"]==4
    assert first["last_result"]["account_app_scope_readback_verified"]
    assert first["last_result"]["native_history_version_ids_available"] is False
    second=run(first,fetch)
    assert second["events_added"]==0 and second["duplicates_skipped"]==8
    assert ws.list_logs()["logs"][0]["events"]==8
    with ws._LOCK:
        db=rest._connect()
        try:
            private=rest._get(db,p["profile_id"])
            case=native_case(private,InputRow(native_case_id="100",native_type="contacts",business_key="fixture"))
            assert case==rest._key(private,"hubspot_case","contacts:100")
            raw=str(db.execute("SELECT * FROM events").fetchall())+str(db.execute("SELECT * FROM objects").fetchall())
        finally:
            db.close()
    for secret in (TOKEN,"private-new-value","private-old-value","private-source","private-label"):
        assert secret not in raw
    for obj in hs.OBJECTS:
        assert "hubspot."+obj+".property.status" in raw


def selected():
    return [{"object_type":"contacts","record_ids":["100"],"properties":["status","owner"]}]


def test_exact_batch_chunking_and_order_independence():
    p=hs.create(config(selections=[{"object_type":"contacts","record_ids":["100","101","102"],"properties":["status","owner"]}],batch_size=2))
    requests=[]
    def fetch(obj,body):
        ids=[r["id"] for r in body["inputs"]];requests.append(ids)
        return batch([record(p,obj,r) for r in reversed(ids)])
    assert run(p,fetch)["events_added"]==6
    assert requests==[["100","101"],["102"]]


@pytest.mark.parametrize("response",[
    lambda p:batch([]),
    lambda p:batch([record(p,"contacts","999")]),
    lambda p:batch([record(p,"contacts")],status="PENDING"),
    lambda p:batch([record(p,"contacts")],numErrors=1),
    lambda p:batch([record(p,"contacts")],paging={"next":{}}),
    lambda p:httpx.Response(207,json={"results":[record(p,"contacts")]}),
])
def test_partial_or_wrong_batch_does_not_commit(response):
    p=hs.create(config(selections=selected()))
    with pytest.raises(rest.FeedError):
        run(p,lambda *_:response(p))
    assert ws.list_logs()["logs"]==[]
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_same_timestamp_versions_fail_closed():
    p=hs.create(config(selections=selected()))
    row=record(p,"contacts")
    row["propertiesWithHistory"]["status"].append({**row["propertiesWithHistory"]["status"][0],"value":"changed"})
    with pytest.raises(rest.FeedError,match="hubspot_ambiguous_same_timestamp_versions"):
        run(p,lambda *_:batch([row]))


def test_missing_history_property_is_counted_not_invented():
    p=hs.create(config(selections=selected(),include_created=False))
    row=record(p,"contacts");del row["propertiesWithHistory"]["owner"]
    result=run(p,lambda *_:batch([row]))
    assert result["events_added"]==1
    assert result["last_result"]["missing_requested_properties"]==1


def test_scope_and_identity_drift_after_read_rolls_back():
    p=hs.create(config(selections=selected()))
    replies=iter([SCOPES,SCOPES+["crm.objects.contacts.write"]])
    with pytest.raises(rest.FeedError,match="hubspot_account_app_or_scope_mismatch"):
        run(p,lambda *_:batch([record(p,"contacts")]),
            identity=lambda:httpx.Response(200,json={"hubId":1001,"appId":2001,"scopes":next(replies)}))
    assert ws.list_logs()["logs"]==[]


def test_account_mismatch_prevents_batch():
    p=hs.create(config(selections=selected()))
    with pytest.raises(rest.FeedError,match="hubspot_account_app_or_scope_mismatch"):
        run(p,lambda *_:pytest.fail("no source read"),identity=lambda:httpx.Response(200,json={"hubId":999,"appId":2001,"scopes":SCOPES}))


def test_multisource_failure_no_partial_retention():
    p=hs.create(config())
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        run(p,lambda obj,body:httpx.Response(503) if obj=="tickets" else batch([record(p,obj)]))
    assert ws.list_logs()["logs"]==[]


def test_rate_limit_cooldown_and_no_redirect():
    p=hs.create(config(selections=selected()))
    with pytest.raises(rest.FeedError,match="provider_rate_limited"):
        run(p,lambda *_:httpx.Response(429,headers={"Retry-After":"120"}))
    assert rest.inventory()["profiles"][0]["retry_not_before"]>rest.time.time()
    with pytest.raises(rest.FeedError,match="provider_retry_cooldown"):
        run(p,lambda *_:pytest.fail("no source read"))


@pytest.mark.parametrize("changes",[
    {"confirm_configuration":False},{"confirm_transient_history_values":False},
    {"confirm_metadata_retention":False},{"confirm_ticket_scope":False},
    {"selections":[{"object_type":"contacts","record_ids":[],"properties":["status"]}]},
    {"selections":[{"object_type":"contacts","record_ids":["100"],"properties":["bad?name"]}]},
    {"source_sha256":"a"*64},
])
def test_scope_requires_explicit_review(changes):
    with pytest.raises(rest.FeedError):
        hs.create(config(**changes))


def test_catalog_and_token_drift(setup,monkeypatch):
    p=hs.create(config())
    setup["primary"]["hub_id"]=999
    monkeypatch.setenv(hs.CATALOG,json.dumps(setup))
    with pytest.raises(rest.FeedError,match="hubspot_source_definition_changed"):
        run(p,lambda *_:pytest.fail("no read"))
    setup["primary"]["hub_id"]=1001
    monkeypatch.setenv(hs.CATALOG,json.dumps(setup))
    monkeypatch.setenv(ENV,"pat-na1-rotated-token")
    with pytest.raises(rest.FeedError,match="credential_changed_reconfigure_required"):
        run(p,lambda *_:pytest.fail("no read"))


def test_gateway_and_passive_inventory():
    p=hs.create(config())
    row=next(x for x in ops.inventory()["profiles"] if x["target_id"]==p["profile_id"])
    assert row["provider"]=="hubspot" and row["provider_live_health"]=="unknown"
    assert TOKEN not in json.dumps(row) and ENV not in json.dumps(row)
    job=JobCreate(organization_id="customer",name="HubSpot",provider="hubspot",
        profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=profile_digest(p),
        interval_minutes=60,confirm_dispatch=True,confirm_counts_only_receipts=True)
    assert job.provider=="hubspot"
    scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=60,
        confirm_scheduled_runs=True,confirm_rest_read_retention=True,expected_rest_profile_revision=p["revision"])
