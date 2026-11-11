"""OCEL pair-binding continuity: only the same original event linking both objects counts."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_binding_continuity as binding
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def document():
    objects = []
    for n in range(1, 10):
        relationships = [{"objectId": f"i{n if n != 9 else 1}", "qualifier": "fulfils"}]
        if n == 1:
            relationships += [{"objectId": "i1", "qualifier": "other-role"}]
        objects.append({"id": f"o{n}", "type": "order", "attributes": [],
                        "relationships": relationships})
        if n <= 8:
            objects.append({"id": f"i{n}", "type": "item", "attributes": [], "relationships": []})
    events = []
    def shared(eid, activity, when, order, item, duplicate_role=False):
        links = [{"objectId": order, "qualifier": "source"},
                 {"objectId": item, "qualifier": "target"}]
        if duplicate_role:
            links += [{"objectId": order, "qualifier": "duplicate"},
                      {"objectId": item, "qualifier": "duplicate"}]
        events.append({"id": eid, "type": activity, "time": f"2026-01-01T{when}:00Z",
                       "attributes": [], "relationships": links})
    def single(eid, activity, when, obj):
        events.append({"id": eid, "type": activity, "time": f"2026-01-01T{when}:00Z",
                       "attributes": [], "relationships": [{"objectId": obj, "qualifier": ""}]})

    shared("o1p", "Pack", "09:00", "o1", "i1", duplicate_role=True)
    shared("o1s", "Ship", "11:00", "o1", "i1")
    shared("o2s", "Ship", "09:00", "o2", "i2")
    shared("o2p", "Pack", "11:00", "o2", "i2")
    shared("o3p", "Pack", "09:00", "o3", "i3")
    shared("o3s", "Ship", "09:00", "o3", "i3")  # distinct original event IDs
    shared("o4p", "Pack", "09:00", "o4", "i4")
    shared("o5s", "Ship", "09:00", "o5", "i5")
    # The objects each take part in Pack and Ship, at identical timestamps,
    # but never in the SAME event. A flattened-case join could falsely pass.
    single("o6p", "Pack", "09:00", "o6")
    single("i6p", "Pack", "09:00", "i6")
    single("o6s", "Ship", "11:00", "o6")
    single("i6s", "Ship", "11:00", "i6")
    shared("o8p1", "Pack", "08:00", "o8", "i8")
    shared("o8p2", "Pack", "09:00", "o8", "i8")
    shared("o8s", "Ship", "10:00", "o8", "i8")
    shared("o9p", "Pack", "10:00", "o9", "i1")  # i1's earlier shared ship is with o1, not o9
    return {"objectTypes": [{"name": "order", "attributes": []}, {"name": "item", "attributes": []}],
            "eventTypes": [], "objects": objects, "events": list(reversed(events))}


def import_log(payload=None):
    return workspace.import_log(BytesIO(json.dumps(payload or document()).encode()),
                                name="Pair binding", source_format="ocel")["log_id"]


def settings(**update):
    return {"source_type": "order", "target_type": "item", "first_activity": "Pack",
            "then_activity": "Ship", **update}


def test_real_same_pair_shared_event_id_and_uncertain_order_are_accounted_independently():
    log = import_log()
    result = binding.analyze(log, settings(sample_limit=50))
    assert result["format"] == binding.FORMAT
    assert result["directed_pairs"] == 9
    assert result["forward_pairs"] == 2
    assert result["reversed_pairs"] == 1
    assert result["simultaneous_timestamp_pairs"] == 1
    assert result["first_only_pairs"] == 2
    assert result["then_only_pairs"] == 1
    assert result["neither_joint_event_pairs"] == 2
    assert result["pairs_with_repeated_joint_activity"] == 1
    assert result["distinct_source_objects"] == 9
    assert result["distinct_target_objects"] == 8  # shared item i1 is not counted twice as a unique item
    assert result["joint_pair_event_witnesses"] == 12
    assert result["distinct_original_joint_events"] == 12
    assert sum(result[k] for k in ("forward_pairs", "reversed_pairs", "simultaneous_timestamp_pairs",
                                  "first_only_pairs", "then_only_pairs", "neither_joint_event_pairs")) == 9

    by_source={r["source_object_id"]:r for r in result["samples"]}
    assert by_source["o1"]["first_joint_events"] == 1  # duplicated OCEL qualifier isn't duplicate evidence
    assert by_source["o1"]["forward_seconds"] == 7200
    assert by_source["o2"]["status"] == "reversed"
    assert by_source["o3"]["status"] == "same_timestamp_order_unknown"
    assert by_source["o6"]["status"] == "neither_joint_event"
    assert by_source["o6"]["first_joint_events"] == by_source["o6"]["then_joint_events"] == 0
    assert by_source["o8"]["first_joint_events"] == 2
    assert by_source["o9"]["status"] == "first_only"
    assert result["same_timestamp_not_treated_as_proven_order"] is True
    assert result["co_attendance_requires_same_event_id"] is True
    assert result["supplied_relationship_valid_at_event_time_verified"] is False
    assert result["full_object_centric_petri_net_binding_replay"] is False
    assert result["execution_authorized"] is False


def test_qualifier_filter_and_extra_relationship_annotations_do_not_multiply_pairs():
    log=import_log()
    assert binding.analyze(log,settings(qualifier="other-role"))["directed_pairs"]==1
    assert binding.analyze(log,settings(qualifier="fulfils"))["directed_pairs"]==9
    assert binding.analyze(log,settings(qualifier=""))["directed_pairs"]==0
    assert binding.analyze(log,settings(source_type="item",target_type="order"))["directed_pairs"]==0
    all_pairs=binding.analyze(log,settings(sample_limit=0))
    assert all_pairs["samples"]==[]
    assert all_pairs["samples_truncated"] is True
    assert all_pairs["forward_pairs"]==2


@pytest.mark.parametrize("bad", [
    {"source_type": ""}, {"target_type": " "}, {"first_activity": "Ship", "then_activity": "Ship"},
    {"first_activity": "Pack\nShip"}, {"then_activity": ""},
    {"qualifier": "x"*81}, {"sample_limit": True}, {"sample_limit": -1},
    {"sample_limit": 51}, {"sample_limit": 2.0}, {"unapproved_field": "anything"},
])
def test_strict_literal_input_validation(bad):
    log=import_log()
    with pytest.raises(workspace.EventLogWorkspaceError):
        binding.analyze(log,settings(**bad))


def test_budgets_fail_closed_before_partial_reporting(monkeypatch):
    log=import_log()
    monkeypatch.setattr(binding,"MAX_PAIRS",8)
    with pytest.raises(workspace.EventLogWorkspaceError,match="distinct directed pairs"):
        binding.analyze(log,settings())
    monkeypatch.setattr(binding,"MAX_PAIRS",20_000)
    monkeypatch.setattr(binding,"MAX_SELECTED_LINKS",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="selected event/object links"):
        binding.analyze(log,settings())
    monkeypatch.setattr(binding,"MAX_SELECTED_LINKS",1_000_000)
    monkeypatch.setattr(binding,"MAX_MATCHED_PAIR_EVENTS",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="jointly attached pair-event"):
        binding.analyze(log,settings())


def test_report_is_read_only_and_pins_exact_imported_graph_revision():
    from server import event_log_object_relationships as relationships
    log=import_log()
    before=relationships.overview(log)
    a=binding.analyze(log,settings())
    b=binding.analyze(log,settings())
    after=relationships.overview(log)
    assert a==b
    assert before["retained_relationships"]==after["retained_relationships"]==10
    with relationships._read_log(log) as (conn,_):
        snap=relationships.snapshot(conn,log)
    assert a["relationship_digest"]==snap["relationships_sha256"]
    assert a["object_index_digest"]==snap["object_index_sha256"]
    assert a["relationship_revision"]==snap["revision"]


def test_real_local_api_uses_dashboard_session_and_rejects_bad_json(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data={"objectTypes":[],"eventTypes":[],"objects":[
  {"id":"o","type":"order","attributes":[],"relationships":[{"objectId":"i","qualifier":"fulfils"}]},
  {"id":"i","type":"item","attributes":[],"relationships":[]}],
  "events":[{"id":"e1","type":"Pack","time":"2026-01-01T09:00:00Z","attributes":[],
      "relationships":[{"objectId":"o","qualifier":""},{"objectId":"i","qualifier":""}]},
      {"id":"e2","type":"Ship","time":"2026-01-01T10:00:00Z","attributes":[],
      "relationships":[{"objectId":"o","qualifier":""},{"objectId":"i","qualifier":""}]}]}
with TestClient(app) as client:
  headers={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
  stored={**headers,"X-OWG-Local-Storage-Confirmed":"true"}
  created=client.post("/v1/event-logs/import?name=binding&format=ocel",json=data,headers=stored)
  assert created.status_code==200,created.text
  url="/v1/event-logs/"+created.json()["log_id"]+"/object-bindings/continuity"
  spec={"source_type":"order","target_type":"item","first_activity":"Pack","then_activity":"Ship"}
  for bad in ({},{"Authorization":"OWG-Session invalid"},
              {"Authorization":"Bearer "+local_auth.ensure_api_token()}):
    assert client.post(url,json=spec,headers=bad).status_code in (401,403)
  result=client.post(url,json=spec,headers=headers)
  assert result.status_code==200,result.text
  assert result.headers["cache-control"]=="no-store"
  assert result.json()["forward_pairs"]==1
  assert result.json()["samples"][0]["forward_seconds"]==3600
  for body in (b'{"sample_limit":1,"sample_limit":2}',b'{"sample_limit":NaN}',
               b'{"untrusted":true}',b'[]',b'{"sample_limit":Infinity}'):
    assert client.post(url,content=body,headers={**headers,"Content-Type":"application/json"}).status_code==422
  assert client.post(url,content=b'{}',headers=headers).status_code==415
  assert client.post(url,content=b'x'*16385,headers={**headers,"Content-Type":"application/json"}).status_code==413
  assert client.get("/object-binding-continuity").headers["content-security-policy"].startswith("default-src 'none'")
'''
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"http-data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"http-auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    result=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],
                          env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
