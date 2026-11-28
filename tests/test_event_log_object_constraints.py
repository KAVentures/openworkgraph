"""Object-centric conformance rules (#370 R24)."""
from __future__ import annotations

from io import BytesIO, StringIO
import csv
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
    evidence = constraints.violations(log, {"rule": items["rule"]})
    assert evidence["violations"] == [{"event_id": None, "object_id": "o1", "detail": "3 related",
                                       "timestamp": None, "has_events": True}]
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


def test_comparison_matches_single_reports_and_omits_unavailable_changes(monkeypatch):
    original = _order_to_cash()
    baseline = _ocel(original)
    for event in original['events']:
        if event['id'] == 'o2-place':
            event['type'] = 'New order'
    comparison = _ocel(original)
    monkeypatch.setattr(constraints, "MAX_RELATION_PAIRS", 1)
    rules = [
        {"template": "event_cardinality", "activity": "Place order", "object_type": "item", "min": 2},
        {"template": "event_cardinality", "activity": "New order", "object_type": "item", "min": 2},
        {"template": "object_cardinality", "object_type": "order", "related_type": "item", "min": 1},
    ]
    result = constraints.compare(baseline, {"other_log_id": comparison, "rules": rules})
    assert result["baseline"] == constraints.check(baseline, {"rules": rules})
    assert result["comparison"] == constraints.check(comparison, {"rules": rules})
    assert result["violation_rate_changes"] == [-0.5, None, None]
    assert result["execution_authorized"] is False
    for other_log_id, message in [(baseline, "different retained event log"), ("missing", "unknown event log")]:
        with pytest.raises(workspace.EventLogWorkspaceError, match=message):
            constraints.compare(baseline, {"other_log_id": other_log_id, "rules": rules})


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


def test_violation_pages_preserve_event_and_object_keys():
    doc = _order_to_cash()
    doc["events"].extend({"id": f"pay{i:02}", "type": "Pay", "time": "2024-01-07T08:00:00Z",
                           "relationships": [{"objectId": "o1", "qualifier": ""}]} for i in range(65))
    log = _ocel(doc)
    rule = {"template": "event_cardinality", "activity": "Pay", "object_type": "order", "min": 2}
    report = constraints.check(log, {"rules": [rule]})
    assert report["results"][0]["examples_truncated"]
    spec = {"rule": rule, "expected_revision": report["log_revision"], "limit": 10}
    page = constraints.violations(log, spec)
    seen = [row["event_id"] for row in page["violations"]]
    while page["next_after"] is not None:
        page = constraints.violations(log, {**spec, "after": page["next_after"]})
        seen.extend(row["event_id"] for row in page["violations"])
    assert len(seen) == page["total"] == report["results"][0]["violating"]
    assert seen == sorted(set(seen)) and set(seen) >= {f"pay{i:02}" for i in range(65)}


def test_synchronization_keeps_ambiguous_display_labels_as_distinct_pairs():
    doc = {"objects": [{"id": key, "type": kind} for key, kind in (("b / c", "order"), ("c", "order"), ("i", "item"))],
           "events": [{"id": key, "type": "Send", "time": "2024-01-01T08:00:00Z",
                       "relationships": [{"objectId": obj, "qualifier": ""} for obj in ("i", focus)]}
                      for key, focus in (("a", "b / c"), ("a / b", "c"))]}
    log = _ocel(doc)
    rule = {"template": "synchronization", "activity": "Send", "object_type": "order",
            "related_type": "item", "required_activity": "Approve"}
    settings = {"rule": rule, "limit": 1}
    first = constraints.violations(log, settings)
    second = constraints.violations(log, {**settings, "after": first["next_after"]})
    assert first["total"] == second["total"] == 2
    assert [(v["event_id"], v["object_id"]) for v in first["violations"] + second["violations"]] == [
        ("a", "b / c"), ("a / b", "c")]
    assert first["violations"][0]["has_events"] and first["violations"][0]["timestamp"] == "2024-01-01T08:00:00Z"
    assert second["next_after"] is None


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
    evidence = {"rule": rule["rules"][0], "expected_revision": result.json()["log_revision"]}
    assert client.post(path+"/violations", json=evidence, headers=bearer).status_code in (401, 403)
    found = client.post(path+"/violations", json=evidence, headers=headers)
    assert found.status_code == 200 and found.headers["cache-control"] == "no-store", found.text
    assert found.json()["violations"] == [{"event_id": "p", "object_id": None,
                                           "detail": "1 linked", "timestamp": "2024-01-01T08:00:00Z"}]
    export_path = path + "/violations/export"
    assert client.post(export_path, json=evidence, headers=bearer).status_code in (401, 403)
    downloaded = client.post(export_path, json=evidence, headers=headers)
    assert downloaded.status_code == 200 and downloaded.headers["cache-control"] == "no-store", downloaded.text
    assert downloaded.headers["x-owg-rows"] == "1" and "review.csv" in downloaded.headers["content-disposition"]
    assert "event_id_review" in downloaded.text and ",p,,2024-01-01T08:00:00Z,1 linked," in downloaded.text
    doc["events"][0]["id"] = "comparison-p"
    other = client.post("/v1/event-logs/import?format=ocel&name=other", content=json.dumps(doc).encode(),
                        headers={**headers, "x-owg-local-storage-confirmed": "true"}).json()["log_id"]
    settings = {**rule, "other_log_id": other}
    assert client.post(path+"/compare", json=settings, headers=bearer).status_code in (401, 403)
    compared = client.post(path+"/compare", json=settings, headers=headers)
    assert compared.status_code == 200 and compared.headers["cache-control"] == "no-store", compared.text
    assert compared.json()["comparison"]["results"][0]["violating_examples"][0]["unit"] == "comparison-p"
    assert compared.json()["violation_rate_changes"] == [0]
    assert client.post(path+"/compare", json={**settings, "case_type": "order"}, headers=headers).status_code == 422
    assert client.post(path, json={"rules": []}, headers=headers).status_code == 422
    assert client.post("/v1/event-logs/log_0000000000000000/object-constraints", json=rule,
                       headers=headers).status_code in (404, 422)
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_synchronization_examples_preserve_literal_event_and_object_keys():
    doc = {'objectTypes': [], 'eventTypes': [],
           'objects': [{'id': 'z', 'type': 'order'}, {'id': 'y / z', 'type': 'order'},
                       {'id': 'i1', 'type': 'item'}, {'id': 'i2', 'type': 'item'}],
           'events': [
               {'id': 's1', 'type': 'Link', 'time': '2024-01-01T00:00:00Z',
                'relationships': [{'objectId': 'z', 'qualifier': ''}, {'objectId': 'i1', 'qualifier': ''}]},
               {'id': 's2', 'type': 'Link', 'time': '2024-01-01T00:00:00Z',
                'relationships': [{'objectId': 'y / z', 'qualifier': ''}, {'objectId': 'i2', 'qualifier': ''}]},
               {'id': 'x / y', 'type': 'Ship', 'time': '2024-01-01T01:00:00Z',
                'relationships': [{'objectId': 'z', 'qualifier': ''}]},
               {'id': 'x', 'type': 'Ship', 'time': '2024-01-01T01:00:00Z',
                'relationships': [{'objectId': 'y / z', 'qualifier': ''}]}]}
    log = _ocel(doc)
    rule = {'template': 'synchronization', 'activity': 'Ship', 'object_type': 'order',
            'related_type': 'item', 'required_activity': 'Pick'}
    [result] = _check(log, rule)
    assert [example['unit'] for example in result['violating_examples']] == ['x / y / z', 'x / y / z']
    exact = {(example['event_id'], example['object_id']) for example in result['violating_examples_exact']}
    assert exact == {('x / y', 'z'), ('x', 'y / z')}
    evidence = constraints.violations(log, {'rule': rule})
    assert {(item['event_id'], item['object_id']) for item in evidence['violations']} == exact


def test_violation_csv_streams_all_rows_and_escapes_formula_labels():
    objects, events = [], []
    for i in range(120):
        order, item = f'+order{i:03}', f'i{i:03}'
        objects.extend([{'id': order, 'type': 'order'}, {'id': item, 'type': 'item'}])
        events.extend([
            {'id': f'link{i:03}', 'type': 'Link', 'time': '2024-01-01T00:00:00Z',
             'relationships': [{'objectId': order, 'qualifier': ''}, {'objectId': item, 'qualifier': ''}]},
            {'id': f'=event{i:03}', 'type': '=Ship', 'time': '2024-01-01T01:00:00Z',
             'relationships': [{'objectId': order, 'qualifier': ''}]},
        ])
    log = _ocel({'objectTypes': [], 'eventTypes': [], 'objects': objects, 'events': events})
    rule = {'template': 'synchronization', 'activity': '=Ship', 'object_type': 'order',
            'related_type': 'item', 'required_activity': 'Pick'}
    report = constraints.check(log, {'rules': [rule]})
    out = StringIO(newline='')
    assert constraints.export_violations(log, {'rule': rule, 'expected_revision': report['log_revision']}, out) == {'violations': 120}
    rows = list(csv.DictReader(StringIO(out.getvalue())))
    assert len(rows) == 120
    assert rows[0]['event_id_review'] == "'=event000" and rows[-1]['object_id_review'] == "'+order119"
    assert rows[0]['activity_review'] == "'=Ship" and rows[0]['timestamp_utc'] == '2024-01-01T01:00:00Z'
    assert len({(row['event_id_review'], row['object_id_review']) for row in rows}) == 120
    with pytest.raises(workspace.EventLogWorkspaceError, match='invalid'):
        constraints.export_violations(log, {'rule': rule}, StringIO())
    with pytest.raises(workspace.EventLogWorkspaceError, match='invalid'):
        constraints.export_violations(log, {'rule': rule, 'expected_revision': 0, 'after': ['x', 'y']}, StringIO())
    new_doc = {'objectTypes': [], 'eventTypes': [], 'objects': [{'id': 'new', 'type': 'order'}],
               'events': [{'id': 'new-event', 'type': 'Link', 'time': '2024-01-02T00:00:00Z',
                           'relationships': [{'objectId': 'new', 'qualifier': ''}]}]}
    workspace.append_log(log, BytesIO(json.dumps(new_doc).encode()), source_format='ocel')
    stale = StringIO()
    with pytest.raises(workspace.EventLogWorkspaceError, match='changed'):
        constraints.export_violations(log, {'rule': rule, 'expected_revision': report['log_revision']}, stale)
    assert stale.getvalue() == ''


def test_violation_csv_requires_graph_pin_and_refuses_incomplete(monkeypatch):
    log = _ocel(_order_to_cash())
    supplied = {'template': 'supplied_object_cardinality', 'object_type': 'order', 'related_type': 'item', 'min': 1, 'qualifier': ''}
    report = constraints.check(log, {'rules': [supplied]})
    with pytest.raises(workspace.EventLogWorkspaceError, match='graph pin'):
        constraints.export_violations(log, {'rule': supplied, 'expected_revision': report['log_revision']}, StringIO())
    basis = report['supplied_relationship_basis']
    pin = {key: basis[key] for key in ('relationships_sha256', 'object_index_sha256')}
    target = StringIO()
    result = constraints.export_violations(log, {'rule': supplied, 'expected_revision': report['log_revision'], 'expected_graph': pin}, target)
    assert result['violations'] == report['results'][0]['violating']
    rows = list(csv.DictReader(StringIO(target.getvalue())))
    assert rows and rows[0]['qualifier_present'] == 'true' and rows[0]['qualifier_review'] == ''
    assert rows[0]['relationships_sha256'] == pin['relationships_sha256']
    assert rows[0]['object_index_sha256'] == pin['object_index_sha256']
    bad = {**pin, 'relationships_sha256': '0' * 64}
    with pytest.raises(workspace.EventLogWorkspaceError, match='changed'):
        constraints.export_violations(log, {'rule': supplied, 'expected_revision': report['log_revision'], 'expected_graph': bad}, StringIO())
    rule = {'template': 'object_cardinality', 'object_type': 'order', 'related_type': 'item', 'min': 1}
    monkeypatch.setattr(constraints, 'MAX_RELATION_PAIRS', 1)
    target = StringIO()
    with pytest.raises(workspace.EventLogWorkspaceError, match='incomplete'):
        constraints.export_violations(log, {'rule': rule, 'expected_revision': report['log_revision']}, target)
    assert target.getvalue() == ''
