"""Flattening diagnostics for object-centric logs (#370 R24)."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_diagnostics as diagnostics
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv
from test_event_log_objects import _ocel, _order_to_cash

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _with_checks():
    """Order-to-cash plus one quality check per item of order 1, each linked to the order and that item,
    and order 1 qualified differently when placed and when paid."""
    doc = _order_to_cash()
    for n, item in enumerate(["i1", "i2", "i3"]):
        doc["events"].append({"id": f"{item}-check", "type": "Check item", "time": f"2024-01-01T12:{n:02d}:00Z",
                              "relationships": [{"objectId": "o1", "qualifier": "checked"},
                                                {"objectId": item, "qualifier": "q"}]})
    for event in doc["events"]:
        if event["id"] == "o1-place":
            event["relationships"][0]["qualifier"] = "placed"
    return doc


def _by_type(result):
    return {t["object_type"]: t for t in result["object_types"]}


def test_flattening_reports_deficiency_convergence_and_divergence():
    result = diagnostics.flattening(_ocel(_with_checks()))
    assert result["events"] == 14 and result["object_type_count"] == 3
    order, item, delivery = (_by_type(result)[t] for t in ("order", "item", "delivery"))

    # Flattened on orders: picks and shipments are not linked to an order and disappear.
    assert order["events_linked"] == 7
    assert order["deficiency"]["events_left_out"] == 7
    assert {a["activity"]: a["events"] for a in order["deficiency"]["by_activity"]} == {"Pick item": 4, "Ship": 3}
    assert order["convergence"]["convergent_events"] == 0 and order["convergence"]["duplication_factor"] == 1
    # Order 1's three checks are one per item: divergence, not rework.
    (check,) = order["divergence"]["by_activity"]
    assert (check["activity"], check["objects_with_repeats"], check["repetitions"]) == ("Check item", 1, 3)
    assert check["repetitions_differ_by"] == [{"object_type": "item", "repeated_groups_linked": 1,
                                               "groups_with_different_objects": 1,
                                               "groups_with_one_object_per_repetition": 1}]

    # Flattened on items: placing order 1 (3 items) and shipping d1 (2 items) are copied into each item's case.
    assert item["events_linked"] == 12
    convergence = item["convergence"]
    assert (convergence["convergent_events"], convergence["flattened_event_rows"], convergence["extra_event_rows"],
            convergence["max_objects_per_event"]) == (2, 15, 3, 3)
    assert convergence["duplication_factor"] == pytest.approx(15 / 12, abs=1e-6)
    assert {a["activity"]: (a["convergent_events"], a["flattened_rows"]) for a in convergence["by_activity"]} == {
        "Place order": (1, 4), "Ship": (1, 4)}
    assert item["deficiency"]["by_activity"] == [{"activity": "Pay", "events": 2}]
    assert item["divergence"]["by_activity"] == []

    assert delivery["events_linked"] == 3 and delivery["deficiency"]["events_left_out"] == 11
    assert order["qualifiers"]["objects_with_several_qualifiers"] == 1
    assert {q["qualifier"]: q["links"] for q in order["qualifiers"]["by_qualifier"]} == {"q": 3, "checked": 3, "placed": 1}
    assert result["divergence_explanation_complete"] is True and result["causal_claims"] is False


def test_case_logs_flatten_without_loss_or_copies():
    log = workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"})["log_id"]
    result = diagnostics.flattening(log)
    (case,) = result["object_types"]
    assert case["deficiency"]["events_left_out"] == 0 and case["convergence"]["extra_event_rows"] == 0
    # Rework in a case log is a repetition with no other object type to explain it.
    assert all(a["repetitions_differ_by"] == [] for a in case["divergence"]["by_activity"])


def test_divergence_explanation_stops_at_its_work_budget(monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_DIVERGENCE_ROWS", 2)
    result = diagnostics.flattening(_ocel(_with_checks()))
    assert result["divergence_explanation_complete"] is False
    assert result["divergence_explanation_unknown_reason"] == "divergence_work_budget"
    assert result["divergence_links_examined"] > 2
    (check,) = _by_type(result)["order"]["divergence"]["by_activity"]
    assert check["repetitions"] == 3 and check["repetitions_differ_by"] == []   # counted, not explained


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = {"objectTypes": [{"name": "order", "attributes": []}, {"name": "item", "attributes": []}], "eventTypes": [],
       "objects": [{"id": "o1", "type": "order"}, {"id": "i1", "type": "item"}, {"id": "i2", "type": "item"}],
       "events": [{"id": "e1", "type": "Place", "time": "2024-01-01T08:00:00Z",
                   "relationships": [{"objectId": "o1", "qualifier": "q"}, {"objectId": "i1", "qualifier": "q"},
                                     {"objectId": "i2", "qualifier": "q"}]}]}
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=ocel&name=o", content=json.dumps(doc).encode(),
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/object-flattening"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get(path, headers=bearer).status_code in (401, 403)
    result = client.get(path, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    item = [t for t in result.json()["object_types"] if t["object_type"] == "item"][0]
    assert item["convergence"]["extra_event_rows"] == 1
    assert client.get("/v1/event-logs/log_0000000000000000/object-flattening", headers=headers).status_code in (404, 422)
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
