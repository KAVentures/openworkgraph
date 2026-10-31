"""Object-centric process model discovery and conformance (#370 R24)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_process_model as ocpm
from server import event_log_workspace as workspace
from test_event_log_objects import _ocel, _order_to_cash

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _labels(type_model):
    return [n["label"] for n in type_model["process_model"]["activities"] if n["kind"] == "activity"]


def _deviating():
    """Order to cash plus order o3, paid before it is placed, by a Pay event that also carries order o2;
    item i5, placed but never picked; and a Pay event linked to no order."""
    doc = _order_to_cash()
    rel = lambda ids: [{"objectId": x, "qualifier": "q"} for x in ids]
    doc["objects"] += [{"id": "o3", "type": "order"}, {"id": "i5", "type": "item"}, {"id": "d4", "type": "delivery"}]
    doc["events"] += [
        {"id": "o3-pay", "type": "Pay", "time": "2024-01-03T07:00:00Z", "relationships": rel(["o3", "o2"])},
        {"id": "o3-place", "type": "Place order", "time": "2024-01-03T08:00:00Z", "relationships": rel(["o3", "i5"])},
        {"id": "d4-ship", "type": "Ship", "time": "2024-01-03T13:00:00Z", "relationships": rel(["d4", "i5"])},
        {"id": "orphan-pay", "type": "Pay", "time": "2024-01-03T19:00:00Z", "relationships": rel(["d4"])},
    ]
    return doc


def test_discovery_gives_each_type_its_lifecycle_and_measures_arcs():
    found = ocpm.discover(_ocel(_order_to_cash()))
    model = found["model"]
    assert model["version"] == ocpm.VERSION
    assert {t["object_type"]: _labels(t) for t in model["types"]} == {
        "item": ["Place order", "Pick item", "Ship"], "delivery": ["Ship"], "order": ["Place order", "Pay"]}
    assert all(t["activities"] == {} for t in model["types"])     # labels equal the activity names
    # One Place order event carries several items and one Ship event two, so those arcs are variable.
    assert {(a["activity"], a["object_type"]): a["variable"] for a in model["arcs"]} == {
        ("Pay", "order"): False, ("Pick item", "item"): False, ("Place order", "item"): True,
        ("Place order", "order"): False, ("Ship", "delivery"): False, ("Ship", "item"): True}
    ship = next(r for r in found["arc_measurements"] if (r["activity"], r["object_type"]) == ("Ship", "item"))
    assert ship["objects_per_event"] == {"min": 1, "max": 2, "mean": round(4 / 3, 6)} and ship["events_with_type"] == 3
    assert found["types_not_discovered"] == [] and found["verified_business_workflow"] is False


def test_a_discovered_model_fits_its_own_log():
    log = _ocel(_order_to_cash())
    result = ocpm.check(log, {"model": ocpm.discover(log)["model"]})
    assert result["summary"] == {"objects": 9, "replayed_objects": 9, "fitting_objects": 9, "fitting_object_share": 1.0,
                                 "events_checked": 11, "fitting_events": 11, "complete": True}
    assert {t["object_type"]: t["token_fitness"] for t in result["lifecycles"]} == {"item": 1.0, "delivery": 1.0, "order": 1.0}
    assert result["arcs"]["issues"] == {"missing_object": 0, "several_objects_on_single_arc": 0, "type_without_arc": 0}
    assert result["binding_based_replay"] is False and result["policy_compliance_verified"] is False


def test_deviations_are_found_in_lifecycles_and_arcs():
    model = ocpm.discover(_ocel(_order_to_cash()))["model"]
    result = ocpm.check(_ocel(_deviating()), {"model": model})
    lifecycles = {t["object_type"]: t for t in result["lifecycles"]}
    # o3 is paid before it is placed, and o2 is paid twice (its own Pay and the o3 Pay that carries it).
    order = lifecycles["order"]
    assert (order["objects"], order["fitting_objects"]) == (3, 1)
    assert order["missing_tokens_by_activity"]["Pay"] >= 1
    # i5 is never picked: one Pick item token is missing before Ship.
    item = lifecycles["item"]
    assert (item["objects"], item["fitting_objects"]) == (5, 4) and item["missing_tokens_by_activity"] == {"Ship": 1}
    assert {v["path"] == ["Place order", "Ship"] for v in item["variants"] if not v["fits"]} == {True}
    # d4 also takes part in a Pay event, which its model lacks: unmodelled, so d4 does not fit.
    delivery = lifecycles["delivery"]
    assert (delivery["objects"], delivery["fitting_objects"]) == (4, 3) and delivery["unmodelled_activities"] == {"Pay": 1}
    arcs = result["arcs"]
    assert arcs["issues"] == {"missing_object": 1, "several_objects_on_single_arc": 1, "type_without_arc": 1}
    assert arcs["examples"] == [
        {"event_id": "o3-pay", "activity": "Pay", "object_type": "order", "issue": "several_objects_on_single_arc", "objects": 2},
        {"event_id": "orphan-pay", "activity": "Pay", "object_type": "delivery", "issue": "type_without_arc", "objects": 1},
        {"event_id": "orphan-pay", "activity": "Pay", "object_type": "order", "issue": "missing_object", "objects": 0}]
    assert (arcs["events_checked"], arcs["fitting_events"]) == (15, 13)


def test_unmodelled_activities_count_as_missing_tokens_not_ignored():
    result = ocpm.check(_ocel(_deviating()), {"model": ocpm.discover(_ocel(_order_to_cash()))["model"]})
    delivery = next(t for t in result["lifecycles"] if t["object_type"] == "delivery")
    assert delivery["missing_tokens_by_activity"] == {"Pay": 1} and delivery["token_fitness"] < 1


def test_normalized_labels_map_back_to_exact_activity_names():
    doc = _order_to_cash()
    for event in doc["events"]:
        event["type"] = {"Pick item": "pick-item (scan)!", "Pay": "Pay"}.get(event["type"], event["type"])
    log = _ocel(doc)
    model = ocpm.discover(log, {"object_types": ["item"]})["model"]
    assert model["types"][0]["activities"] == {"pick-item scan": "pick-item (scan)!"}
    assert [t["object_type"] for t in model["types"]] == ["item"]
    result = ocpm.check(log, {"model": model})
    assert result["summary"]["fitting_objects"] == 4 and result["arcs"]["issues"]["type_without_arc"] == 0
    # Without the mapping, the exact name is unmodelled and the label never happens.
    bare = copy.deepcopy(model)
    bare["types"][0]["activities"] = {}
    item = ocpm.check(log, {"model": bare})["lifecycles"][0]
    assert item["fitting_objects"] == 0 and item["unmodelled_activities"] == {"pick-item (scan)!": 4}
    assert item["remaining_tokens_by_activity"]


def test_an_edited_arc_changes_only_the_arc_check():
    log = _ocel(_order_to_cash())
    model = ocpm.discover(log)["model"]
    for arc in model["arcs"]:
        if (arc["activity"], arc["object_type"]) == ("Place order", "item"):
            arc["variable"] = False
    model["arcs"] = [a for a in model["arcs"] if (a["activity"], a["object_type"]) != ("Pay", "order")]
    result = ocpm.check(log, {"model": model})
    assert result["summary"]["fitting_objects"] == 9
    assert result["arcs"]["issues"] == {"missing_object": 0, "several_objects_on_single_arc": 1, "type_without_arc": 2}
    assert {(r["activity"], r["object_type"], r["issue"]): r["events"] for r in result["arcs"]["issues_by_arc"]} == {
        ("Place order", "item", "several_objects_on_single_arc"): 1, ("Pay", "order", "type_without_arc"): 2}


def test_long_lifecycles_and_the_variant_limit_are_counted_as_unknown():
    doc = _order_to_cash()
    doc["objects"].append({"id": "big", "type": "item"})
    doc["events"] += [{"id": f"big-{k}", "type": "Pick item", "time": f"2024-02-01T{k // 60:02d}:{k % 60:02d}:00Z",
                       "relationships": [{"objectId": "big", "qualifier": "q"}]} for k in range(ocpm.MAX_TRACE_LENGTH + 1)]
    log = _ocel(doc)
    model = ocpm.discover(_ocel(_order_to_cash()))["model"]
    item = next(t for t in ocpm.check(log, {"model": model})["lifecycles"] if t["object_type"] == "item")
    assert item["unknown_object_reasons"]["trace_length_limit"] == 1 and item["complete"] is False
    assert item["replayed_objects"] == 4 and item["fitting_object_share_of_all_lower_bound"] == 4 / 5
    order = next(t for t in ocpm.check(_ocel(_deviating()), {"model": model, "max_variants": 1})["lifecycles"]
                 if t["object_type"] == "order")
    assert order["unknown_object_reasons"]["variant_limit"] == order["objects"] - order["replayed_objects"] > 0


@pytest.mark.parametrize("change, message", [
    (lambda m: m.update(version="v0"), "version"),
    (lambda m: m["types"].append(copy.deepcopy(m["types"][0])), "one model"),
    (lambda m: m["arcs"].append(dict(m["arcs"][0])), "one arc"),
    (lambda m: m["types"][0].update(object_type="invoice"), "unknown object type"),
    (lambda m: m["arcs"][0].update(object_type="invoice"), "unknown object type"),
    (lambda m: m["types"][0]["process_model"].update(transitions=[]), "not a valid process model"),
    (lambda m: m["types"][0].update(activities={"Nope": "Pick item"}), "no activity labelled Nope"),
    (lambda m: m["types"][0].update(activities={"Place order": "Ship", "Ship": "Ship"}), "same activity"),
    (lambda m: m["arcs"][0].update(variable="yes"), "variable"),
    (lambda m: m.update(extra=1), "extra"),
])
def test_invalid_models_are_refused(change, message):
    log = _ocel(_order_to_cash())
    model = ocpm.discover(log)["model"]
    change(model)
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        ocpm.check(log, {"model": model})


def test_invalid_discovery_settings_are_refused():
    log = _ocel(_order_to_cash())
    for spec, message in (({"object_types": ["invoice"]}, "unknown object type"), ({"noise": 1}, "noise"),
                          ({"object_types": []}, "object_types"), ({"activities": 0}, "activities"),
                          ({"other": 1}, "other"), ([], "object"), (0, "object")):
        with pytest.raises(workspace.EventLogWorkspaceError, match=message):
            ocpm.discover(log, spec)
    with pytest.raises(workspace.EventLogWorkspaceError, match="max_variants"):
        ocpm.check(log, {"model": ocpm.discover(log)["model"], "max_variants": ocpm.MAX_VARIANTS + 1})


def test_a_type_that_cannot_be_discovered_is_reported_not_dropped(monkeypatch):
    log = _ocel(_order_to_cash())
    real = ocpm.discovery.discover

    def failing(log_id, case_type, **kwargs):
        if case_type == "delivery":
            raise workspace.EventLogWorkspaceError("too many activities")
        return real(log_id, case_type, **kwargs)
    monkeypatch.setattr(ocpm.discovery, "discover", failing)
    found = ocpm.discover(log)
    assert found["types_not_discovered"] == [{"object_type": "delivery", "reason": "too many activities"}]
    assert ("Ship", "delivery") not in {(a["activity"], a["object_type"]) for a in found["model"]["arcs"]}


def test_eight_types_at_the_catalog_size_limit_fit_in_one_request():
    """Each type model is bounded by the catalog (40 nodes, 100 transitions, 40 rules)."""
    from io import BytesIO
    import random
    rng, objects_, events = random.Random(1), [], []
    for t in range(ocpm.MAX_TYPES):
        for o in range(40):
            oid = f"t{t}o{o}"
            objects_.append({"id": oid, "type": f"type{t}"})
            steps = [f"Activity number {t} step {s}" for s in range(14) if s < 2 or rng.random() < 0.6]
            events += [{"id": f"{oid}-{s}", "type": a, "time": f"2024-01-{1 + s:02d}T{o % 24:02d}:00:00Z",
                        "relationships": [{"objectId": oid, "qualifier": ""}]} for s, a in enumerate(steps)]
    doc = {"objectTypes": [], "eventTypes": [], "objects": objects_, "events": events}
    log = workspace.import_log(BytesIO(json.dumps(doc).encode()), name="wide", source_format="ocel")["log_id"]
    found = ocpm.discover(log)
    assert len(found["model"]["types"]) == ocpm.MAX_TYPES
    size = len(json.dumps({"model": found["model"]}))
    assert 65536 < size < ocpm.MAX_REQUEST_BYTES / 2
    assert ocpm.check(log, {"model": found["model"]})["summary"]["complete"]


def test_real_http_routes_require_a_dashboard_session(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = {"objectTypes": [], "eventTypes": [],
       "objects": [{"id": "o1", "type": "order"}, {"id": "i1", "type": "item"}],
       "events": [{"id": "p", "type": "Place order", "time": "2024-01-01T08:00:00Z",
                   "relationships": [{"objectId": "o1", "qualifier": ""}, {"objectId": "i1", "qualifier": ""}]},
                  {"id": "y", "type": "Pay", "time": "2024-01-02T08:00:00Z",
                   "relationships": [{"objectId": "o1", "qualifier": ""}]}]}
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=ocel&name=o", content=json.dumps(doc).encode(),
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    base = "/v1/event-logs/" + made.json()["log_id"] + "/object-process-model/"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(base + "discovery", json={}, headers=bearer).status_code in (401, 403)
    found = client.post(base + "discovery", json={}, headers=headers)
    assert found.status_code == 200 and found.headers["cache-control"] == "no-store", found.text
    model = found.json()["model"]
    assert client.post(base + "conformance", json={"model": model}, headers=bearer).status_code in (401, 403)
    checked = client.post(base + "conformance", json={"model": model}, headers=headers)
    assert checked.status_code == 200 and checked.headers["cache-control"] == "no-store", checked.text
    assert checked.json()["summary"]["fitting_objects"] == 2
    assert client.post(base + "conformance", json={"model": {}}, headers=headers).status_code == 422
    json_headers = {**headers, "Content-Type": "application/json"}
    # Models of up to eight types exceed the usual 64 KiB body limit; the route allows 256 KiB.
    assert client.post(base + "conformance", content='{"model": {}}' + " " * 100_000, headers=json_headers).status_code == 422
    assert client.post(base + "conformance", content='{"model": {}}' + " " * 300_000, headers=json_headers).status_code == 413
    assert client.post(base + "discovery", content="{}" + " " * 100_000, headers=json_headers).status_code == 413
    repeated = '{"model": 1, "model": 2}'
    assert client.post(base + "conformance", content=repeated, headers={**headers, "Content-Type": "application/json"}).status_code == 422
    assert client.post("/v1/event-logs/log_0000000000000000/object-process-model/discovery", json={},
                       headers=headers).status_code in (404, 422)
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_one_request_reads_every_type_from_the_same_revision(monkeypatch):
    """An append waiting on the workspace lock cannot land between two types of one check."""
    log = _ocel(_order_to_cash())
    model = ocpm.discover(log)["model"]
    real, seen = ocpm._lifecycles, []

    def slow(log_id, type_model, max_variants):
        seen.append(workspace._LOCK._is_owned())
        return real(log_id, type_model, max_variants)
    monkeypatch.setattr(ocpm, "_lifecycles", slow)
    ocpm.check(log, {"model": model})
    assert seen == [True, True, True]

