"""Agents can read the object-centric map of a shared log, with rare types, activities and links withheld."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _document():
    """6 orders with 2 items each; orders are placed (with their items) and paid, o0 is also cancelled.
    Items are picked and shipped on one of 2 deliveries (a type with too few objects to show)."""
    objects, events = [], []
    rel = lambda ids: [{"objectId": x, "qualifier": "q"} for x in ids]  # noqa: E731
    for k in range(6):
        order, items, delivery = f"o{k}", [f"i{k}a", f"i{k}b"], f"d{k // 3}"
        objects += [{"id": order, "type": "order"}] + [{"id": i, "type": "item"} for i in items]
        day = f"2024-01-{1 + k:02d}"
        events.append({"id": f"{order}-place", "type": "Place order", "time": f"{day}T08:00:00Z", "relationships": rel([order, *items])})
        for n, i in enumerate(items):
            events.append({"id": f"{i}-pick", "type": "Pick item", "time": f"{day}T{9 + n:02d}:00:00Z", "relationships": rel([i])})
            events.append({"id": f"{i}-ship", "type": "Ship", "time": f"{day}T{12 + n:02d}:00:00Z", "relationships": rel([i, delivery])})
        if k == 0:
            events.append({"id": "o0-cancel", "type": "Cancel", "time": f"{day}T15:00:00Z", "relationships": rel([order])})
        events.append({"id": f"{order}-pay", "type": "Pay", "time": f"{day}T18:00:00Z", "relationships": rel([order])})
    objects += [{"id": "d0", "type": "delivery"}, {"id": "d1", "type": "delivery"}]
    return {"objectTypes": [{"name": t, "attributes": []} for t in ("order", "item", "delivery")],
            "eventTypes": [], "objects": objects, "events": events}


def _log():
    return workspace.import_log(BytesIO(json.dumps(_document()).encode()), name="o", source_format="ocel")["log_id"]


def test_object_map_with_floor():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.object_map(log)
    sharing.set_share(log, "aggregates")
    r = sharing.object_map(log)
    types = {t["object_type"]: t for t in r["object_types"]}
    assert set(types) == {"order", "item"} and r["withheld"]["object_types"] == 1
    item = types["item"]
    assert {(e["from"], e["to"]): e["case_count"] for e in item["transitions"]} == {
        ("Place order", "Pick item"): 12, ("Pick item", "Ship"): 12}
    assert item["start_activities"] == {"Place order": 12} and item["end_activities"] == {"Ship": 12}
    order = types["order"]
    assert {a["activity"] for a in order["activities"]} == {"Place order", "Pay"}      # Cancel: one order
    assert [(e["from"], e["to"]) for e in order["transitions"]] == [("Place order", "Pay")]
    assert r["withheld"]["activities"] == 1 and r["withheld"]["transitions"] >= 2
    assert all("max_events_per_object" not in t for t in r["object_types"])
    assert {(x["from_type"], x["to_type"]) for x in r["relations"]} <= {("order", "item"), ("item", "order")}
    assert all("max_linked_per_object" not in x and "delivery" not in (x["from_type"], x["to_type"]) for x in r["relations"])
    assert "Cancel" not in repr(r) and "o0" not in repr(r) and r["format"] == sharing.FORMAT
    shared = {x["activity"]: x["object_types"] for x in r["activities_touching_several_types"]}
    assert shared == {"Place order": ["item", "order"]}           # Ship also touches deliveries, which are hidden


def test_repeated_activity_of_one_object_is_not_named():
    doc = _document()
    rel = lambda ids: [{"objectId": x, "qualifier": "q"} for x in ids]  # noqa: E731
    doc["events"] += [{"id": f"o0-audit{k}", "type": "Audit", "time": f"2024-01-01T{19 + k % 4:02d}:0{k}:00Z",
                       "relationships": rel(["o0", "i0a"])} for k in range(6)]
    log = workspace.import_log(BytesIO(json.dumps(doc).encode()), name="a", source_format="ocel")["log_id"]
    sharing.set_share(log, "aggregates")
    assert "Audit" not in repr(sharing.object_map(log))


def test_agent_route(tmp_path):
    code = r'''
import json
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
from tests.test_process_intelligence_object_map import _document
log = workspace.import_log(BytesIO(json.dumps(_document()).encode()), name="q", source_format="ocel")["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/process-intelligence/'+log+'/object-map'
    assert client.get(url,headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    r=client.get(url,headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert sorted(t['object_type'] for t in r.json()['object_types'])==['item','order']
    assert client.get(url).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
