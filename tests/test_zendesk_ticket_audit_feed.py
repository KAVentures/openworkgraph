"""Selected Zendesk audit feed tests using the real retained workspace."""
from datetime import timedelta
import json

import httpx
import pytest

from server import zendesk_ticket_audit_feed as zd
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from shared.enterprise_connector_contract import capabilities

HOST = "fixture.zendesk.com"
CONN = "conn_"+"a"*16
TOKEN = "fixture-private-bearer"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DATA_DIR", tmp_path)
    connection = {"connection_id": CONN, "provider": "zendesk", "host": HOST,
                  "scopes": ["read"], "has_refresh_token": True,
                  "reauthorization_required": False}
    monkeypatch.setattr(zd.oauth, "status", lambda: {"connections":[connection]})
    monkeypatch.setattr(zd.oauth, "api_host", lambda _: HOST)
    monkeypatch.setattr(zd.oauth, "access_token", lambda _: TOKEN)
    return connection


def config(**extra):
    return {"name":"Fixture", "connection_id":CONN, "instance_host":HOST,
            "principal_id":"99", "ticket_ids":["12"], "fields":["status","assignee_id"],
            "initial_since":rest._iso((rest._now()-timedelta(days=3)).replace(microsecond=0)),
            "confirm_configuration":True, "confirm_transient_audit_payloads":True,
            "confirm_metadata_retention":True, **extra}


def audits(p):
    at = rest._stamp(p["config"]["initial_since"])+timedelta(hours=23,minutes=30)
    return [{"id":100,"ticket_id":12,"created_at":rest._iso(at),"author_id":55,
             "events":[{"id":101,"type":"Create","field_name":"status","value":"open"},
                       {"id":102,"type":"Comment","body":"private-comment"},
                       {"id":103,"type":"Change","field_name":"assignee_id","value":"777",
                        "previous_value":"888"}]}]


def run(p, records, *, change=None):
    seen=[]
    def handler(req):
        assert req.method=="GET" and req.url.host==HOST
        assert req.headers["authorization"]=="Bearer "+TOKEN
        seen.append(req.url.path)
        if req.url.path=="/api/v2/users/me.json":
            payload={"user":{"id":99,"active":True,"role":"agent","email":"private-email"}}
        else:
            assert req.url.path=="/api/v2/tickets/12/audits.json"
            assert req.url.params["per_page"]=="100"
            payload={"audits":json.loads(json.dumps(records)),"count":len(records),"next_page":None}
        if change:
            override=change(payload,len(seen))
            if isinstance(override,httpx.Response):
                return override
        return httpx.Response(200,json=payload)
    def factory(**kwargs):
        assert not kwargs["follow_redirects"] and not kwargs["trust_env"]
        return httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,
                    client_factory=factory),seen


def test_retention_repeated_scan_and_overlap_replay():
    p=zd.create(config())
    rows=audits(p)
    first,seen=run(p,rows)
    assert first["events_added"]==2
    assert len(seen)==4
    assert first["last_result"]["principal_readback_verified"]
    assert first["last_result"]["repeat_scan_agreement"]
    assert not first["last_result"]["provider_completeness_verified"]
    second,_=run(first,rows)
    assert second["events_added"]==0 and second["duplicates_skipped"]==2
    assert ws.list_logs()["logs"][0]["events"]==2
    with ws._LOCK:
        c=rest._connect()
        try:
            values=c.execute("SELECT activity,resource,attributes FROM events WHERE log_id=?",
                             [first["log_id"]]).fetchall()
            assert {r[0] for r in values}=={"zendesk.status.open","zendesk.assignee_id.set"}
            assert all(len(r[1])==64 for r in values)
            raw=str(values)+str(c.execute("SELECT * FROM event_source_keys").fetchall())
        finally:
            c.close()
    assert all(x not in raw for x in (TOKEN,"private-comment","private-email"))
    assert rest.schedule_pin(p["profile_id"])==p["revision"]
    assert capabilities("zendesk")["exact_snapshot_publication"]


@pytest.mark.parametrize("kind",["wrong_principal","suspended","ticket","count","missing_page",
    "duplicate_audit","duplicate_child","unknown_value","scan_drift","redirect","rate_limit","external_next"])
def test_failed_window_never_retains_partial_evidence(kind):
    p=zd.create(config())
    def change(payload,step):
        if kind=="wrong_principal" and "user" in payload:
            payload["user"]["id"]=100
        elif kind=="suspended" and "user" in payload:
            payload["user"]["suspended"]=True
        elif "audits" in payload:
            a=payload["audits"][0]
            if kind=="ticket": a["ticket_id"]=13
            elif kind=="count": payload["count"]=2
            elif kind=="missing_page": payload.pop("next_page")
            elif kind=="duplicate_audit":
                payload["audits"].append(a);payload["count"]=2
            elif kind=="duplicate_child": a["events"].append(a["events"][0])
            elif kind=="unknown_value": a["events"][0]["value"]="unreviewed"
            elif kind=="scan_drift" and step==3: a["events"][0]["value"]="pending"
            elif kind=="redirect": return httpx.Response(302,headers={"Location":"https://other.invalid"})
            elif kind=="rate_limit": return httpx.Response(429,headers={"Retry-After":"120"})
            elif kind=="external_next":
                payload["count"]=2;payload["next_page"]="https://other.invalid/api/v2/tickets/12/audits.json?page=2"
    with pytest.raises(rest.FeedError):
        run(p,audits(p),change=change)
    live=rest.inventory()["profiles"][0]
    assert live["checkpoint"] is None and live["log_id"] is None
    assert ws.list_logs()["logs"]==[]


def test_passive_creation_and_scope_drift(setup,monkeypatch):
    monkeypatch.setattr(zd.oauth,"access_token",lambda _:pytest.fail("unexpected refresh"))
    p=zd.create(config())
    assert rest.schedule_pin(p["profile_id"])==p["revision"]
    setup["scopes"]=["read","write"]
    with pytest.raises(rest.FeedError,match="scope_mismatch"):
        rest.schedule_pin(p["profile_id"])


@pytest.mark.parametrize("change",[
    {"instance_host":"fixture.zendesk.com.evil.invalid"},
    {"ticket_ids":["12","12"]},{"ticket_ids":["12","13","14","15","16"]},
    {"fields":["comment"]},{"principal_id":"0"},{"confirm_transient_audit_payloads":False},
    {"confirm_metadata_retention":False},{"window_hours":1,"overlap_minutes":60},
])
def test_reviewed_scope_refusals(change):
    with pytest.raises(rest.FeedError):
        zd.create(config(**change))


def test_pagination_reconstructs_requests_and_counts_all_pages():
    p=zd.create(config())
    base=audits(p)[0]
    data=[]
    for i in range(101):
        a=json.loads(json.dumps(base))
        a["id"]=1000+i
        a["events"]=[{"id":2000+i,"type":"Change","field_name":"status","value":"open"}]
        data.append(a)
    paths=[]
    def handler(req):
        if req.url.path.endswith("me.json"):
            return httpx.Response(200,json={"user":{"id":99,"active":True,"role":"agent"}})
        page=req.url.params["page"]
        paths.append(page)
        return httpx.Response(200,json={"audits":data[:100] if page=="1" else data[100:],
            "count":101,"next_page":"https://"+HOST+"/api/v2/tickets/12/audits.json?page=2" if page=="1" else None})
    def factory(**kw): return httpx.Client(transport=httpx.MockTransport(handler),**kw)
    result=rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,client_factory=factory)
    assert paths==["1","2","1","2"]
    assert result["events_added"]==101
