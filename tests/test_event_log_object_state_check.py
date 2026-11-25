"""As-of attribute checks against real retained OCEL fixtures."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_workspace as w
from server import event_log_object_state_check as s


@pytest.fixture(autouse=True)
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(w,"DATA_DIR",tmp_path)


def fixture():
    def obj(ident,values):
        return {"id":ident,"type":"order","attributes":[{"name":"status","time":at,"value":v} for at,v in values]}
    def ts(hour):
        return f"2024-01-01T{hour:02d}:00:00Z"
    def event(ident,h,links):
        return {"id":ident,"type":"Pay","time":ts(h),"relationships":[{"objectId":o,"qualifier":q} for o,q in links]}
    return {"objectTypes":[{"name":"order","attributes":[]},{"name":"invoice","attributes":[]}],"eventTypes":[],
            "objects":[obj("a",[("1970-01-01T00:00:00Z","created"),(ts(3),"approved")]),
                       obj("b",[(ts(4),"approved")]),
                       obj("c",[(ts(1),"created"),(ts(1),"approved"),(ts(5),"approved")]),
                       obj("d",[]),{"id":"i","type":"invoice","attributes":[]}],
            "events":[event("e1",2,[("a",""),("a","payer")]),
                      event("e2",3,[("a",""),("b","")]),
                      event("e3",4,[("a",""),("b","")]),
                      event("e4",2,[("c","")]),
                      event("e5",6,[("c","")]),
                      event("e6",2,[("d","")]),
                      event("e7",2,[("i","")]),
                      event("e8",2,[("a",""),("d","")])]}


def load():
    return w.import_log(BytesIO(json.dumps(fixture()).encode()),name="orders",source_format="ocel")["log_id"]


def spec(**kwargs):
    return {"activity":"Pay","object_type":"order","attribute":"status","expected_value":"approved",**kwargs}


def test_asof_values_conflict_resolution_missing_and_quantifiers():
    log=load()
    a=s.analyze(log,spec())
    assert a["selected_events"]==8 and a["selected_distinct_event_object_links"]==10
    assert a["event_counts"]=={"matched":2,"not_matched":2,"unknown":3,"no_linked_object":1}
    assert a["object_link_counts"]=={"matched":4,"not_matched":2,"missing_history":2,
                                      "before_first_value":1,"ambiguous_value":1}
    rows={r["event_id"]:r for r in a["rows"]}
    assert rows["e1"]["linked_objects"]==1
    assert rows["e2"]["object_witnesses"][0]["recorded_value"]=="approved"
    assert rows["e2"]["object_witnesses"][0]["valid_from"]=="2024-01-01T03:00:00Z"
    assert rows["e4"]["object_witnesses"][0]["status"]=="ambiguous_value"
    assert rows["e5"]["status"]=="matched"
    assert rows["e7"]["status"]=="no_linked_object"
    any_=s.analyze(log,spec(quantifier="any"))
    assert any_["event_counts"]=={"matched":3,"not_matched":1,"unknown":3,"no_linked_object":1}
    assert not any_["policy_compliance_verified"]


def test_exact_qualifier_empty_and_ne():
    log=load()
    result=s.analyze(log,spec(event_qualifier="payer",operator="ne"))
    assert result["event_counts"]=={"matched":1,"not_matched":0,"unknown":0,"no_linked_object":7}
    assert result["selected_distinct_event_object_links"]==1
    assert s.analyze(log,spec(event_qualifier=""))["selected_distinct_event_object_links"]==10


def test_filtered_pagination_and_revision_rejection():
    log=load()
    first=s.analyze(log,spec(status="unknown",limit=1))
    assert first["matching_events"]==3 and first["next_offset"]==1
    second=s.analyze(log,spec(status="unknown",limit=1,offset=1,expected_revision=first["revision"]))
    assert first["rows"][0]["event_id"]!=second["rows"][0]["event_id"]
    with pytest.raises(w.EventLogWorkspaceError,match="page one"):
        s.analyze(log,spec(offset=1))
    with pytest.raises(w.EventLogWorkspaceError,match="changed"):
        s.analyze(log,spec(expected_revision=first["revision"]+1))


@pytest.mark.parametrize("bad",[{"limit":True},{"quantifier":"none"},{"operator":"gt"},{"extra":1},
                              {"activity":""},{"offset":1}])
def test_invalid_requests(bad):
    with pytest.raises(w.EventLogWorkspaceError):
        s.analyze(load(),spec(**bad))


def test_work_bounds(monkeypatch):
    log=load()
    monkeypatch.setattr(s,"MAX_EVENTS",1)
    with pytest.raises(w.EventLogWorkspaceError,match="event bound"):
        s.analyze(log,spec())
    monkeypatch.setattr(s,"MAX_EVENTS",100)
    monkeypatch.setattr(s,"MAX_LINKS",1)
    with pytest.raises(w.EventLogWorkspaceError,match="links"):
        s.analyze(log,spec())


def test_http_auth_and_strict_json(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_object_state_check import load,spec
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    log=load();url="/v1/event-logs/"+log+"/object-state-check"
    assert c.post(url,json=spec(),headers=bearer).status_code in (401,403)
    r=c.post(url,json=spec(),headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["event_counts"]["matched"]==2
    assert c.post(url,content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    result=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
