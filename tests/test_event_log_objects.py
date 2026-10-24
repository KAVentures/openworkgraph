"""Object-centric process maps for the event-log workspace (roadmap #326 B)."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_objects as objects
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv
from test_event_log_workspace import OCEL

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _ocel(document) -> str:
    return workspace.import_log(BytesIO(json.dumps(document).encode()), name="o", source_format="ocel")["log_id"]


def _order_to_cash():
    """Two orders; order 1 has three items and two deliveries, order 2 one item and one delivery."""
    objects_, events = [], []
    for o, items, deliveries in (("o1", ["i1", "i2", "i3"], ["d1", "d2"]), ("o2", ["i4"], ["d3"])):
        objects_.append({"id": o, "type": "order"})
        objects_ += [{"id": i, "type": "item"} for i in items] + [{"id": d, "type": "delivery"} for d in deliveries]
        day = "01" if o == "o1" else "02"
        rel = lambda ids: [{"objectId": x, "qualifier": "q"} for x in ids]
        events.append({"id": f"{o}-place", "type": "Place order", "time": f"2024-01-{day}T08:00:00Z", "relationships": rel([o, *items])})
        for n, i in enumerate(items):
            events.append({"id": f"{i}-pick", "type": "Pick item", "time": f"2024-01-{day}T{9 + n:02d}:00:00Z", "relationships": rel([i])})
        for n, d in enumerate(deliveries):
            carried = items[n::len(deliveries)]
            events.append({"id": f"{d}-ship", "type": "Ship", "time": f"2024-01-{day}T1{3 + n}:00:00Z", "relationships": rel([d, *carried])})
        events.append({"id": f"{o}-pay", "type": "Pay", "time": f"2024-01-{day}T18:00:00Z", "relationships": rel([o])})
    return {"objectTypes": [{"name": t, "attributes": []} for t in ("order", "item", "delivery")],
            "eventTypes": [], "objects": objects_, "events": events}


def test_each_object_type_gets_its_own_lifecycle_map():
    graph = objects.object_graph(_ocel(_order_to_cash()))
    types = {t["object_type"]: t for t in graph["object_types"]}
    assert {k: (v["objects"], v["events"]) for k, v in types.items()} == {"item": (4, 9), "order": (2, 4), "delivery": (3, 3)}
    item = types["item"]
    assert {(e["from"], e["to"]): e["transition_count"] for e in item["transitions"]} == {
        ("Place order", "Pick item"): 4, ("Pick item", "Ship"): 4}
    assert item["start_activities"] == {"Place order": 4} and item["end_activities"] == {"Ship": 4}
    order = types["order"]
    assert [(e["from"], e["to"]) for e in order["transitions"]] == [("Place order", "Pay")]
    assert order["transitions"][0]["median_elapsed_gap_seconds"] == 10 * 3600
    assert types["delivery"]["start_activities"] == types["delivery"]["end_activities"] == {"Ship": 3}


def test_relations_count_objects_linked_by_shared_events():
    graph = objects.object_graph(_ocel(_order_to_cash()))
    rel = {(r["from_type"], r["to_type"]): r for r in graph["relations"]}
    assert rel[("order", "item")]["mean_linked_per_object"] == 2.0 and rel[("order", "item")]["max_linked_per_object"] == 3
    assert rel[("item", "order")]["share_of_from_objects"] == 1.0 and rel[("item", "order")]["max_linked_per_object"] == 1
    assert rel[("delivery", "item")]["linked_objects_total"] == 4
    assert ("order", "delivery") not in rel  # never share an event
    shared = {a["activity"]: a["object_types"] for a in graph["activities_touching_several_types"]}
    assert shared == {"Place order": ["item", "order"], "Ship": ["delivery", "item"]}
    assert graph["links_are_shared_events_not_verified_relationships"] is True and graph["causal_claims"] is False


def test_single_event_objects_start_and_end_with_that_event():
    graph = objects.object_graph(_ocel(OCEL))
    item = next(t for t in graph["object_types"] if t["object_type"] == "item")
    assert item["start_activities"] == {"place": 2} and item["end_activities"] == {"place": 1, "ship": 1}


def test_flat_logs_have_one_type_and_no_relations():
    log_id = workspace.import_log(BytesIO(_claims_csv().encode()), name="c", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    graph = objects.object_graph(log_id)
    assert [t["object_type"] for t in graph["object_types"]] == ["case"]
    assert graph["relations"] == [] and graph["activities_touching_several_types"] == []
    with pytest.raises(workspace.EventLogWorkspaceError):
        objects.object_graph("log_0000000000000000")


def test_activity_cap_keeps_maps_drawable(monkeypatch):
    monkeypatch.setattr(objects, "MAX_ACTIVITIES_PER_TYPE", 2)
    item = next(t for t in objects.object_graph(_ocel(_order_to_cash()))["object_types"] if t["object_type"] == "item")
    assert item["activities_truncated"] is True and len(item["activities"]) == 2
    names = {a["activity"] for a in item["activities"]}
    assert all(e["from"] in names and e["to"] in names for e in item["transitions"])
    assert item['activity_types']==3 and item['transition_types']==2
    assert item['transitions_truncated'] is True


def test_map_limits_are_exact_and_occurrences_are_not_case_support(monkeypatch):
    from server import event_log_cases
    doc={'objects':[{'id':'o','type':'order'},{'id':'i','type':'item'}], 'events':[]}
    for n, activity in enumerate(['A','B','A','B']):
        doc['events'].append({'id':str(n),'type':activity,'time':'2024-01-01T00:00:00Z',
            'relationships':[{'objectId':'o','qualifier':'q'},{'objectId':'o','qualifier':'other'}]+
                ([{'objectId':'i','qualifier':'q'}] if n in (0,3) else [])})
    log=_ocel(doc)
    monkeypatch.setattr(objects,'MAX_TYPES',2)
    monkeypatch.setattr(objects,'MAX_ACTIVITIES_PER_TYPE',2)
    monkeypatch.setattr(objects,'MAX_EDGES_PER_TYPE',2)
    graph=objects.object_graph(log)
    assert graph['object_type_count']==2 and graph['object_types_truncated'] is False
    order=next(t for t in graph['object_types'] if t['object_type']=='order')
    assert not order['activities_truncated'] and not order['transitions_truncated']
    edge=next(e for e in order['transitions'] if e['from']=='A' and e['to']=='B')
    assert edge['transition_count']==2 and edge['case_count']==1 and edge['median_elapsed_gap_seconds']==0
    for kind,key,length in [('order','o',4),('item','i',2)]:
        cases=event_log_cases.cases(log,{'transition':['A','B']},kind)
        assert cases['selected_cases']==1 and cases['cases'][0]['case_id']==key
        assert cases['cases'][0]['events']==length and cases['cases'][0]['timestamp_ties']==length-1
    monkeypatch.setattr(objects,'MAX_EDGES_PER_TYPE',1)
    order=next(t for t in objects.object_graph(log)['object_types'] if t['object_type']=='order')
    assert order['transition_types']==2 and order['transitions_truncated'] is True


def test_real_http_object_map_route(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

doc = {"objectTypes": [], "eventTypes": [], "objects": [{"id": "a", "type": "order"}, {"id": "b", "type": "item"}],
       "events": [{"id": "e", "type": "Place", "time": "2024-01-01T00:00:00Z",
                   "relationships": [{"objectId": "a", "qualifier": ""}, {"objectId": "b", "qualifier": ""}]}]}
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=ocel&name=t", content=json.dumps(doc).encode(),
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    url = "/v1/event-logs/" + made.json()["log_id"] + "/object-map"
    assert client.get(url).status_code == 401
    graph = client.get(url, headers=headers)
    assert graph.status_code == 200 and len(graph.json()["object_types"]) == 2, graph.text
    assert graph.headers["cache-control"] == "no-store"
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_events_with_several_qualifiers_to_one_object_count_once():
    doc = {"objectTypes": [], "eventTypes": [], "objects": [{"id": "o", "type": "order"}, {"id": "i", "type": "item"}],
           "events": [
               {"id": "a", "type": "Place", "time": "2024-01-01T08:00:00Z",
                "relationships": [{"objectId": "o", "qualifier": "buyer"}, {"objectId": "o", "qualifier": "payer"},
                                  {"objectId": "i", "qualifier": "line"}]},
               {"id": "b", "type": "Ship", "time": "2024-01-01T09:00:00Z",
                "relationships": [{"objectId": "o", "qualifier": "q"}, {"objectId": "i", "qualifier": "line"}]}]}
    order = next(t for t in objects.object_graph(_ocel(doc))["object_types"] if t["object_type"] == "order")
    assert [(e["from"], e["to"], e["transition_count"]) for e in order["transitions"]] == [("Place", "Ship", 1)]
    assert {a["activity"]: a["event_count"] for a in order["activities"]} == {"Place": 1, "Ship": 1}
