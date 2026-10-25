"""Object-centric conformance rules (#370 R24)."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_constraints as constraints
from server import event_log_workspace as workspace
from test_event_log_objects import _ocel, _order_to_cash

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _check(log, *rules):
    return constraints.check(log, {"rules": list(rules)})["results"]


def _counts(result):
    return (result["activated"], result["violating"], result["vacuously_satisfied"])


def test_event_cardinality_counts_linked_objects_per_event():
    log = _ocel(_order_to_cash())
    ship, place = _check(log, {"template": "event_cardinality", "activity": "Ship", "object_type": "delivery",
                               "min": 1, "max": 1},
                         {"template": "event_cardinality", "activity": "Place order", "object_type": "item", "min": 2})
    assert _counts(ship) == (3, 0, 0) and ship["unit"] == "events"
    # Order 2 is placed with one item.
    assert _counts(place) == (2, 1, 0)
    assert place["violating_examples"] == [{"unit": "o2-place", "detail": "1 linked"}]
    assert place["violation_rate"] == 0.5 and 0 < place["violation_rate_ci95"][0] < 0.5 < place["violation_rate_ci95"][1]


def test_object_cardinality_relates_objects_through_shared_events():
    log = _ocel(_order_to_cash())
    items, delivered, shipped = _check(
        log, {"template": "object_cardinality", "object_type": "order", "related_type": "item", "min": 1, "max": 2},
        {"template": "object_cardinality", "object_type": "delivery", "related_type": "order", "min": 1},
        {"template": "object_cardinality", "object_type": "item", "related_type": "delivery", "via_activity": "Ship",
         "min": 1, "max": 1})
    assert _counts(items) == (2, 1, 0) and items["violating_examples"] == [{"unit": "o1", "detail": "3 related"}]
    # Deliveries never share an event with an order, so none is related to one.
    assert _counts(delivered) == (3, 3, 0)
    assert _counts(shipped) == (4, 0, 0) and shipped["description"].endswith("through Ship events")


def test_synchronization_checks_every_related_object_before_the_trigger():
    log = _ocel(_order_to_cash())
    pay, place, ship, vacuous = _check(
        log, {"template": "synchronization", "activity": "Pay", "object_type": "order", "related_type": "item",
              "required_activity": "Pick item"},
        {"template": "synchronization", "activity": "Place order", "object_type": "order", "related_type": "item",
         "required_activity": "Pick item"},
        {"template": "synchronization", "activity": "Ship", "object_type": "delivery", "related_type": "item",
         "required_activity": "Pick item"},
        {"template": "synchronization", "activity": "Pay", "object_type": "order", "related_type": "delivery",
         "required_activity": "Ship"})
    assert _counts(pay) == (2, 0, 0) and pay["unit"] == "event_object_pairs"
    # Items are picked after their order is placed.
    assert _counts(place) == (2, 2, 0)
    assert place["violating_examples"] == [{"unit": "o1-place / o1", "detail": "0 related without it, 3 only later"},
                                           {"unit": "o2-place / o2", "detail": "0 related without it, 1 only later"}]
    assert _counts(ship) == (3, 0, 0)
    assert _counts(vacuous) == (0, 0, 2) and vacuous["violation_rate"] is None
    assert vacuous["required_activity_in_log"] is True and "required_activity_in_log" not in _check(
        log, {"template": "event_cardinality", "activity": "Ship", "object_type": "delivery", "min": 1})[0]


def test_synchronization_orders_ties_by_event_id_and_counts_missing_objects():
    doc = {"objectTypes": [], "eventTypes": [],
           "objects": [{"id": "o", "type": "order"}, {"id": "a", "type": "item"}, {"id": "b", "type": "item"},
                       {"id": "c", "type": "item"}],
           "events": [{"id": "e0", "type": "Add", "time": "2024-01-01T08:00:00Z",
                       "relationships": [{"objectId": x, "qualifier": ""} for x in ("o", "a", "b", "c")]},
                      {"id": "e1", "type": "Pick", "time": "2024-01-01T09:00:00Z", "relationships": [{"objectId": "a", "qualifier": ""}]},
                      {"id": "e3", "type": "Pick", "time": "2024-01-01T10:00:00Z", "relationships": [{"objectId": "b", "qualifier": ""}]},
                      {"id": "e2", "type": "Ship", "time": "2024-01-01T10:00:00Z", "relationships": [{"objectId": "o", "qualifier": ""}]}]}
    [result] = _check(_ocel(doc), {"template": "synchronization", "activity": "Ship", "object_type": "order",
                                   "related_type": "item", "required_activity": "Pick"})
    # a is picked before; b at the same time with a later event ID, so after; c is never picked.
    assert result["violating_examples"] == [{"unit": "e2 / o", "detail": "1 related without it, 1 only later"}]


def test_rules_disclose_absent_names_and_the_work_budget(monkeypatch):
    log = _ocel(_order_to_cash())
    [absent] = _check(log, {"template": "event_cardinality", "activity": "Refund", "object_type": "invoice", "min": 1})
    assert absent["activity_in_log"] is False and absent["object_type_in_log"] is False and absent["units"] == 0
    monkeypatch.setattr(constraints, "MAX_RELATION_PAIRS", 1)
    result = constraints.check(log, {"rules": [
        {"template": "object_cardinality", "object_type": "order", "related_type": "item", "min": 1},
        {"template": "event_cardinality", "activity": "Ship", "object_type": "delivery", "min": 1}]})
    unknown, exact = result["results"]
    assert unknown["complete"] is False and unknown["unknown_reason"] == "relation_work_budget" and "violating" not in unknown
    assert exact["complete"] and result["rules_unknown"] == 1 and result["execution_authorized"] is False


@pytest.mark.parametrize("rule, message", [
    ({"template": "event_cardinality", "activity": "A", "object_type": "t", "min": 3, "max": 1}, "below its min"),
    ({"template": "object_cardinality", "object_type": "t", "related_type": "u"}, "min above 0 or a max"),
    ({"template": "synchronization", "activity": "A", "object_type": "t", "related_type": "t", "required_activity": "A"},
     "different activity or object type"),
    ({"template": "event_cardinality", "activity": "A", "object_type": "t", "min": 1, "sql": "x"}, "invalid"),
    ({"template": "cypher"}, "invalid"),
])
def test_invalid_rules_are_refused(rule, message):
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        constraints.parse({"rules": [rule]})


def test_rule_count_is_bounded():
    rule = {"template": "event_cardinality", "activity": "A", "object_type": "t", "min": 1}
    with pytest.raises(workspace.EventLogWorkspaceError):
        constraints.parse({"rules": [rule] * (constraints.MAX_RULES + 1)})


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = {"objectTypes": [], "eventTypes": [],
       "objects": [{"id": "o1", "type": "order"}, {"id": "i1", "type": "item"}],
       "events": [{"id": "p", "type": "Pay", "time": "2024-01-01T08:00:00Z",
                   "relationships": [{"objectId": "o1", "qualifier": ""}, {"objectId": "i1", "qualifier": ""}]}]}
rule = {"rules": [{"template": "event_cardinality", "activity": "Pay", "object_type": "order", "min": 2}]}
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=ocel&name=o", content=json.dumps(doc).encode(),
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/object-constraints"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(path, json=rule, headers=bearer).status_code in (401, 403)
    result = client.post(path, json=rule, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert result.json()["results"][0]["violating"] == 1
    assert client.post(path, json={"rules": []}, headers=headers).status_code == 422
    assert client.post("/v1/event-logs/log_0000000000000000/object-constraints", json=rule,
                       headers=headers).status_code in (404, 422)
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
