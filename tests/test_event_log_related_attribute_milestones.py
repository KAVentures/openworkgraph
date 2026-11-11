"""Temporal target-object state at independently keyed source/target OCEL milestones (R24)."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_related_attribute_milestones as milestones
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _value(value, when):
    return {"name": "status", "time": when, "value": value}


def _document():
    initial = "1970-01-01T00:00:00Z"
    when = lambda h: f"2026-01-01T{h}:00:00Z"
    histories = {
        "i1": [_value("draft", initial), _value("approved", when("10")), _value("shipped", when("12"))],
        "i2": [_value("paid", when("11"))],
        "i3": [_value("open", when("08")), _value("blocked", when("08"))],
        "i4": [],
        "i5": [_value("accepted", when("08"))],
        "i6": [_value("received", when("08"))],
        "i7": [_value("ready", when("07"))],
        "i8": [_value("confirmed", when("10")), _value("confirmed", when("10"))],
    }
    objects = []
    for n in range(1, 9):
        objects.append({"id": f"o{n}", "type": "order", "attributes": [],
                        "relationships": [{"objectId": f"i{n}", "qualifier": "billed"},
                                          *([{"objectId": f"i{n}", "qualifier": "duplicate"}] if n == 1 else [])]})
        objects.append({"id": f"i{n}", "type": "invoice", "attributes": histories[f"i{n}"],
                        "relationships": []})
    # Two orders to one invoice: the target is shared but both directed pairs are counted.
    objects.append({"id": "o9", "type": "order", "attributes": [],
                    "relationships": [{"objectId": "i1", "qualifier": "billed"}]})
    events = []
    def event(eid, time, activity, obj, multiple=False):
        events.append({"id": eid, "type": activity, "time": when(time), "attributes": [],
                       "relationships": [{"objectId": obj, "qualifier": ""},
                                         *([{"objectId": obj, "qualifier": "also"}] if multiple else [])]})
    for source, target, ts, tt in [
        (1, 1, "09", "11"), (2, 2, "09", "12"),
        (3, 3, "09", "10"), (4, 4, "09", "10"),
        (5, 5, None, "09"), (6, 6, "12", "09"),
        (7, 7, "09", None), (8, 8, "10", "10"),
        (9, 1, "10", None)]:
        if ts is not None:
            event("s"+str(source), ts, "Approve", "o"+str(source), multiple=source==1)
        if tt is not None and not any(e["id"] == "t"+str(target) for e in events):
            event("t"+str(target), tt, "Settle", "i"+str(target))
    return {"objectTypes": [{"name": "order", "attributes": []},
                            {"name": "invoice", "attributes": []}],
            "eventTypes": [], "objects": objects, "events": list(reversed(events))}


def _import(document=None):
    doc = document or _document()
    return workspace.import_log(BytesIO(json.dumps(doc).encode()), name="As-of invoice",
                                source_format="ocel")["log_id"]


def _settings(**changes):
    return {"source_type": "order", "target_type": "invoice",
            "source_activity": "Approve", "target_activity": "Settle",
            "target_attribute": "status", **changes}


def test_state_at_other_object_milestone_is_not_the_stored_first_attribute():
    log = _import()
    r = milestones.analyze(log, _settings(sample_limit=50))
    assert r["format"] == milestones.FORMAT
    assert r["pairs"] == 9  # o1->i1 twice qualified, but still one pair
    assert r["distinct_source_objects"] == 9 and r["distinct_target_objects"] == 8
    assert r["known_at_source_pairs"] == 5
    assert r["known_at_target_pairs"] == 7
    assert r["missing_source_activity_pairs"] == 1
    assert r["missing_target_activity_pairs"] == 2
    assert r["target_attribute_never_recorded_pairs"] == 1
    assert r["target_attribute_recorded_later_pairs"] == 1
    assert r["ambiguous_same_timestamp_pairs"] == 1
    assert r["different_known_values_across_milestones_pairs"] == 2
    assert r["same_known_values_across_milestones_pairs"] == 2
    assert r["reversed_activity_order_pairs"] == 1
    assert r["initial_untimed_value_used_at_source_pairs"] == 1
    by_source = {row["source_object_id"]: row for row in r["sample_pairs"]}
    assert by_source["o1"]["target_value_at_source_activity"] == "draft"
    assert by_source["o1"]["target_value_at_target_activity"] == "approved"
    assert by_source["o2"]["at_source_status"] == "attribute_recorded_only_later"
    assert by_source["o2"]["target_value_at_source_activity"] is None
    assert by_source["o3"]["at_source_status"] == "ambiguous_same_effective_time"
    assert by_source["o4"]["at_source_status"] == "attribute_never_recorded"
    assert by_source["o5"]["at_source_status"] == "source_activity_missing"
    assert by_source["o6"]["target_value_at_source_activity"] == "received"
    assert by_source["o8"]["target_value_at_source_activity"] == "confirmed"
    assert by_source["o9"]["target_value_at_source_activity"] == "approved"
    assert r["unit"] == "distinct_directed_caller_supplied_object_pair"
    assert r["relationship_validity_at_event_time_verified"] is False
    assert r["causal_claims"] is False
    assert r["business_action_authorized"] is False
    assert sum(x["pairs"] for x in r["at_source_value_groups"]) == r["pairs"]


def test_exact_qualifier_repeats_and_missing_attributes_cannot_inflate_pairs():
    log = _import()
    assert milestones.analyze(log, _settings(qualifier="duplicate"))["pairs"] == 1
    assert milestones.analyze(log, _settings(qualifier="billed"))["pairs"] == 9
    empty = milestones.analyze(log, _settings(qualifier=""))
    assert empty["pairs"] == 0 and empty["at_source_value_groups"] == []
    unknown = milestones.analyze(log, _settings(target_attribute="unrecorded"))
    assert unknown["pairs"] == 9
    assert unknown["target_attribute_never_recorded_pairs"] == 8  # o5 still lacks source milestone
    assert unknown["known_at_source_pairs"] == 0
    assert unknown["known_at_target_pairs"] == 0
    assert unknown["sample_pairs"][0]["target_value_at_source_activity"] is None


@pytest.mark.parametrize("change", [
    {"source_type": ""}, {"target_type": " "},
    {"source_activity": "bad\nvalue"}, {"target_attribute": ""},
    {"qualifier": "x"*81}, {"sample_limit": True},
    {"sample_limit": 51}, {"target_attribute": None},
    {"cursor": {"after": ["a"]}}, {"sample_limit": -1},
])
def test_strict_bounded_settings_refuse_unsupported_inputs(change):
    log = _import()
    with pytest.raises(workspace.EventLogWorkspaceError):
        milestones.analyze(log, _settings(**change))


def test_pair_link_and_attribute_work_budgets_refuse_partial_cohorts(monkeypatch):
    log = _import()
    monkeypatch.setattr(milestones, "MAX_PAIRS", 8)
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most"):
        milestones.analyze(log, _settings())
    monkeypatch.setattr(milestones, "MAX_PAIRS", 20_000)
    monkeypatch.setattr(milestones, "MAX_LINKS", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="event-link work budget"):
        milestones.analyze(log, _settings())
    monkeypatch.setattr(milestones, "MAX_LINKS", 1_000_000)
    monkeypatch.setattr(milestones, "MAX_ATTRIBUTE_VALUES", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="timed-value work budget"):
        milestones.analyze(log, _settings())


def test_no_data_mutated_and_same_snapshot_digests_as_other_relationship_reports():
    from server import event_log_object_relationships as relationships
    log = _import()
    before = relationships.overview(log)
    first = milestones.analyze(log, _settings(sample_limit=0))
    second = milestones.analyze(log, _settings(sample_limit=0))
    after = relationships.overview(log)
    assert first == second
    assert first["sample_pairs"] == []
    assert first["pairs"] == 9 and first["samples_truncated"]
    assert before["retained_relationships"] == after["retained_relationships"]
    with relationships._read_log(log) as (conn, _):
        snap = relationships.snapshot(conn, log)
    assert first["relationship_digest"] == snap["relationships_sha256"]
    assert first["object_index_digest"] == snap["object_index_sha256"]


def test_authenticated_http_only_and_strict_json_refusal_in_real_local_app(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc={"objectTypes":[],"eventTypes":[],"objects":[
  {"id":"o","type":"order","attributes":[],"relationships":[{"objectId":"i","qualifier":"billed"}]},
  {"id":"i","type":"invoice","attributes":[{"name":"status","time":"1970-01-01T00:00:00Z","value":"draft"}],"relationships":[]}],
  "events":[{"id":"e1","type":"Approve","time":"2026-01-01T09:00:00Z","attributes":[],
      "relationships":[{"objectId":"o","qualifier":""}]},
      {"id":"e2","type":"Settle","time":"2026-01-01T10:00:00Z","attributes":[],
      "relationships":[{"objectId":"i","qualifier":""}]}]}
with TestClient(app) as client:
  owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
  stored={**owner,"X-OWG-Local-Storage-Confirmed":"true"}
  created=client.post("/v1/event-logs/import?name=asof&format=ocel",json=doc,headers=stored)
  assert created.status_code==200,created.text
  url="/v1/event-logs/"+created.json()["log_id"]+"/object-relationships/attribute-milestones"
  spec={"source_type":"order","target_type":"invoice","source_activity":"Approve",
        "target_activity":"Settle","target_attribute":"status"}
  for headers in [{},{"Authorization":"OWG-Session invalid"},
                  {"Authorization":"Bearer "+local_auth.ensure_api_token()}]:
    assert client.post(url,json=spec,headers=headers).status_code in (401,403)
  result=client.post(url,json=spec,headers=owner)
  assert result.status_code==200,result.text
  assert result.headers["cache-control"]=="no-store"
  j=result.json()
  assert j["pairs"]==1 and j["known_at_source_pairs"]==1
  assert j["sample_pairs"][0]["target_value_at_source_activity"]=="draft"
  for content in [b'{"sample_limit":1,"sample_limit":2}',b'{"sample_limit":NaN}',
                  b'{"unknown":true}',b'[]']:
    assert client.post(url,content=content,headers={**owner,"Content-Type":"application/json"}).status_code==422
  assert client.post(url,content=b'{}',headers=owner).status_code==415
  assert client.post(url,content=b'x'*16385,headers={**owner,"Content-Type":"application/json"}).status_code==413
'''
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"http-data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"http-auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"http-config.json")}
    r=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],
                     env=env,capture_output=True,text=True,timeout=120)
    assert r.returncode==0,r.stdout+"\n"+r.stderr
