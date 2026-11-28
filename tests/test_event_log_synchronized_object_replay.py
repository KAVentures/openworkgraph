"""Independent acceptance of original-event synchronized object replay."""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_process_model as ocpm
from server import event_log_synchronized_object_replay as sync
from server import event_log_workspace as workspace
from test_event_log_objects import _ocel, _order_to_cash


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


@pytest.fixture
def baseline():
    log = _ocel(_order_to_cash())
    return log, ocpm.discover(log)["model"]


def rows(log, model, report, view, **settings):
    return sync.review(log, {"model":model, "view":view,
                             "expected_review_sha256":report["review_sha256"], **settings})["page"]


def fixture_distinct_memberships(document):
    # Independent fixture oracle: qualifier multiplicity is not membership.
    return len({(event["id"], link["objectId"])
                for event in document["events"] for link in event["relationships"]})


def test_order_to_cash_shared_events_counted_once_and_all_objects_complete(baseline):
    log, model = baseline
    result = sync.review(log, {"model":model})
    assert result["source_population"]["selected_events"] == 11
    assert result["source_population"]["selected_objects"] == 9
    # Place: 6 memberships, Pick: 4, Ship: 7, Pay: 2.
    assert fixture_distinct_memberships(_order_to_cash()) == 19
    assert result["source_population"]["distinct_selected_memberships"] == 19
    assert result["component_status_counts"] == {
        "complete":2,"unfinished":0,"deviation":0,"unknown":0,"no_events":0}
    assert result["event_status_counts"] == {"accepted":11,"deviation":0,"not_replayed":0,"unknown":0}
    assert result["object_status_counts"] == {"terminal_possible":9}
    assert result["all_selected_components_complete"]
    assert not result["general_ocpn_replay"] and not result["execution_authorized"]
    assert sync.review(log, {"model":model}) == result


def test_exact_component_and_event_membership_paging(baseline):
    log,model = baseline
    result = sync.review(log, {"model":model})
    first = rows(log,model,result,"components",limit=1)
    second = rows(log,model,result,"components",limit=1,offset=first["next_offset"])
    assert first["total"] == second["total"] == 2
    assert first["rows"][0]["component_id"] != second["rows"][0]["component_id"]
    assert second["next_offset"] is None
    component = next(c for c in first["rows"]+second["rows"] if c["objects"] == 6)
    key = component["component_id"]
    ev = rows(log,model,result,"events",component_id=key)["rows"]
    assert len(ev) == 7
    first_event = next(e for e in ev if e["event_id"]=="o1-place")
    assert first_event["linked_modeled_object_count"] == 4
    a = rows(log,model,result,"participants",component_id=key,event_id="o1-place",limit=2)
    b = rows(log,model,result,"participants",component_id=key,event_id="o1-place",limit=2,offset=a["next_offset"])
    assert {o["object_id"] for o in a["rows"]+b["rows"]} == {"o1","i1","i2","i3"}
    assert b["next_offset"] is None
    assert rows(log,model,result,"objects",component_id=key,status="terminal_possible")["total"] == 6
    with pytest.raises(workspace.EventLogWorkspaceError,match="not in this reviewed component"):
        rows(log,model,result,"participants",component_id=key,event_id="o2-place")


def test_atomic_failure_stops_only_affected_component(baseline):
    _,model = baseline
    doc = _order_to_cash()
    # d1-ship includes i1 and i3. Remove only i3's required Pick event.
    doc["events"] = [e for e in doc["events"] if e["id"]!="i3-pick"]
    log = _ocel(doc)
    result = sync.review(log, {"model":model})
    assert result["component_status_counts"]["complete"] == 1
    assert result["component_status_counts"]["deviation"] == 1
    c = rows(log,model,result,"components",status="deviation")["rows"][0]
    assert c["reason"] == "atomic_event_not_enabled"
    assert c["first_deviation_event_id"] == "d1-ship"
    events = rows(log,model,result,"events",component_id=c["component_id"])["rows"]
    failed = next(e for e in events if e["status"]=="deviation")
    assert failed["blocked_objects"] == [{"object_id":"i3","object_type":"item","reason":"activity_not_enabled"}]
    assert failed["blocked_object_count"] == 1
    assert all(e["status"]=="not_replayed" for e in events[events.index(failed)+1:])
    assert c["object_status_counts"] == {"unknown":6}
    assert result["population_by_component_status"]["complete"]["objects"] == 3


def test_missing_required_binding_and_unmodeled_linked_activity(baseline):
    _,model = baseline
    doc = _order_to_cash()
    pay = next(e for e in doc["events"] if e["id"]=="o1-pay")
    pay["relationships"] = []
    # Some importers reject empty relationship lists. Give the event an
    # unselected type instead so it remains a retained original event.
    doc["objectTypes"].append({"name":"invoice","attributes":[]})
    doc["objects"].append({"id":"inv","type":"invoice"})
    pay["relationships"] = [{"objectId":"inv","qualifier":"q"}]
    log = _ocel(doc)
    result = sync.review(log, {"model":model})
    candidates = rows(log,model,result,"components",status="deviation")["rows"]
    c = next(c for c in candidates if c["first_deviation_event_id"]=="o1-pay")
    event = rows(log,model,result,"events",component_id=c["component_id"])["rows"][0]
    assert event["issues"] == [{"reason":"missing_required_object_type","object_type":"order","objects":0}]
    assert event["linked_modeled_object_count"] == 0
    assert result["source_population"]["excluded_objects"] == 1
    assert result["component_status_counts"]["unfinished"] == 1
    doc2 = _order_to_cash()
    doc2["events"].append({"id":"mystery","type":"Unexpected task","time":"2024-01-02T19:00:00Z",
                           "relationships":[{"objectId":"o2","qualifier":"q"}]})
    log2 = _ocel(doc2)
    r2 = sync.review(log2, {"model":model})
    c2 = rows(log2,model,r2,"components",status="deviation")["rows"][0]
    failure = rows(log2,model,r2,"events",component_id=c2["component_id"],status="deviation")["rows"][0]
    assert {i["reason"] for i in failure["issues"]} == {"linked_type_without_arc","activity_not_in_lifecycle"}


def test_shared_object_timestamp_tie_unknown_but_disjoint_tie_safe(baseline):
    _,model = baseline
    doc = _order_to_cash()
    # Simultaneous picks on different items commute, even in one component.
    next(e for e in doc["events"] if e["id"]=="i2-pick")["time"] = "2024-01-01T09:00:00Z"
    log = _ocel(doc)
    assert sync.review(log, {"model":model})["component_status_counts"]["complete"] == 2
    # Now i1's Pick and Place share the same object and time.
    next(e for e in doc["events"] if e["id"]=="i1-pick")["time"] = "2024-01-01T08:00:00Z"
    log = _ocel(doc)
    result = sync.review(log, {"model":model})
    assert result["component_status_counts"]["unknown"] == 1
    assert result["component_status_counts"]["complete"] == 1
    c = rows(log,model,result,"components",status="unknown")["rows"][0]
    assert c["reason"] == "shared_object_timestamp_tie" and c["ambiguous_order_object_count"] == 1
    assert c["event_status_counts"]["unknown"] == c["events"]


def test_terminal_prefix_and_registered_object_with_no_events(baseline):
    _,model = baseline
    doc = _order_to_cash()
    doc["events"] = [e for e in doc["events"] if e["id"]!="o1-pay"]
    doc["objects"].append({"id":"isolated","type":"order"})
    log = _ocel(doc)
    result = sync.review(log, {"model":model})
    assert result["component_status_counts"]["unfinished"] == 1
    assert result["component_status_counts"]["no_events"] == 1
    c = rows(log,model,result,"components",status="unfinished")["rows"][0]
    assert c["event_status_counts"]["accepted"] == c["events"]
    objects = rows(log,model,result,"objects",component_id=c["component_id"],status="terminal_not_reachable")
    assert [o["object_id"] for o in objects["rows"]] == ["o1"]


def test_qualifier_multiplicity_does_not_duplicate_participants(baseline):
    _,model = baseline
    doc = _order_to_cash()
    event = next(e for e in doc["events"] if e["id"]=="o1-pay")
    event["relationships"].append({"objectId":"o1","qualifier":"second"})
    log = _ocel(doc)
    result = sync.review(log, {"model":model})
    assert result["component_status_counts"]["complete"] == 2
    assert fixture_distinct_memberships(doc) == fixture_distinct_memberships(_order_to_cash()) == 19
    assert result["source_population"]["distinct_selected_memberships"] == 19


def test_shape_multiple_objects_on_single_arc(baseline):
    log,model = baseline
    altered = deepcopy(model)
    next(a for a in altered["arcs"] if a["activity"]=="Place order" and a["object_type"]=="item")["variable"] = False
    result = sync.review(log, {"model":altered})
    c = rows(log,altered,result,"components",status="deviation")["rows"][0]
    failed = rows(log,altered,result,"events",component_id=c["component_id"],status="deviation")["rows"][0]
    assert failed["event_id"] == "o1-place"
    assert failed["issues"] == [{"reason":"multiple_objects_on_single_arc","object_type":"item","objects":3}]


def test_budget_unknown_and_population_caps_fail_explicitly(baseline, monkeypatch):
    log,model = baseline
    monkeypatch.setattr(sync,"MAX_COMPONENT_WORK",1)
    result = sync.review(log, {"model":model})
    assert result["component_status_counts"]["unknown"] == 2
    assert result["component_status_counts"]["deviation"] == 0
    assert sum(result["event_status_counts"].values()) == 11
    for limit in ("MAX_EVENTS","MAX_OBJECTS","MAX_LINKS"):
        with monkeypatch.context() as m:
            m.setattr(sync,limit,0)
            with pytest.raises(workspace.EventLogWorkspaceError,match="complete .*budget"):
                sync.review(log, {"model":model})


def test_model_and_source_pins_reject_drift(baseline):
    log,model = baseline
    r = sync.review(log, {"model":model})
    changed = deepcopy(model)
    changed["arcs"][0]["variable"] = not changed["arcs"][0]["variable"]
    with pytest.raises(workspace.EventLogWorkspaceError,match="basis changed"):
        rows(log,changed,r,"components")
    with workspace._LOCK:
        c = workspace._connect()
        try:
            c.execute("UPDATE events SET ts=? WHERE log_id=? AND event_id=?",
                      [datetime(2024,1,1,19),log,"o1-pay"])
        finally:
            c.close()
    with pytest.raises(workspace.EventLogWorkspaceError,match="basis changed"):
        rows(log,model,r,"components")


@pytest.mark.parametrize("spec",[
    {"view":"events"},{"view":"components","status":"accepted"},{"status":"deviation"},
    {"offset":1},{"limit":True},{"limit":101},{"event_id":"o1-pay"},{"unknown":1},
    {"view":"participants","component_id":"a"*64,"expected_review_sha256":"a"*64},
])
def test_strict_request_validation(baseline,spec):
    log,model = baseline
    with pytest.raises(workspace.EventLogWorkspaceError):
        sync.review(log, {"model":model,**spec})


def test_exact_nondeterministic_marking_preserves_alternative_paths():
    # A decision after A may go to B or C. The observed choice is C;
    # neither branch is arbitrarily preselected by the replay.
    from shared.process_model import ProcessModel
    model = ProcessModel.model_validate({
        "goal":"Choose a route", "baseline_path":["A","C"],"baseline_source":"user_declared",
        "activities":[{"id":"a","label":"A"},{"id":"x","label":"Choice","kind":"decision"},
                      {"id":"b","label":"B"},{"id":"c","label":"C"}],
        "business_rules":[{"id":"rb","text":"Choose B"},{"id":"rc","text":"Choose C"}],
        "transitions":[{"source":"a","target":"x"},{"source":"x","target":"b","rule_id":"rb"},{"source":"x","target":"c","rule_id":"rc"}],
        "entry_activity_id":"a","terminal_activity_ids":["b","c"]})
    life = sync.Lifecycle(model,{"A":"A","B":"B","C":"C"})
    budget = sync.Budget()
    marks = life.advance({life.net.initial},"A",budget)
    assert life.advance(marks,"B",budget) and life.advance(marks,"C",budget)
    assert life.terminal(life.advance(marks,"C",budget),budget)
    assert not life.advance(marks,"A",budget)


def test_real_local_http_authorization_and_strict_body(tmp_path):
    script = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server import event_log_object_process_model as ocpm
from test_event_log_objects import _ocel,_order_to_cash
with TestClient(app) as client:
    log=_ocel(_order_to_cash())
    model=ocpm.discover(log)["model"]
    path='/v1/event-logs/'+log+'/object-process-model/synchronized-replay'
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert client.post(path,json={"model":model},headers=bearer).status_code in (401,403)
    r=client.post(path,json={"model":model},headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["component_status_counts"]["complete"]==2
    assert client.get('/synchronized-object-replay').status_code==200
    assert client.get('/synchronized-object-replay.js').status_code==200
    assert client.post(path,content=b'{"model":{},"model":{}}',
                       headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    run=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+script],
                       cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stdout+run.stderr
