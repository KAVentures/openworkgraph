"""Observed lead times across supplied, directed OCEL object relationships (R24)."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_lead_times as leads
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _document():
    """Seven distinct O2O pairs; two source→target qualifiers on the first."""
    objects = []
    for n in range(1, 8):
        objects.append({
            "id": f"o{n}", "type": "order", "attributes": [],
            "relationships": [
                {"objectId": f"d{n}", "qualifier": "ships"},
                *([{"objectId": f"d{n}", "qualifier": "alternate"}] if n == 1 else []),
            ],
        })
        objects.append({"id": f"d{n}", "type": "delivery", "attributes": [], "relationships": []})
    events = []

    def event(event_id, when, activity, obj, *, duplicate_qualifier=False):
        links = [{"objectId": obj, "qualifier": ""}]
        if duplicate_qualifier:
            links.append({"objectId": obj, "qualifier": "repeated-link-to-one-event"})
        events.append({"id": event_id, "type": activity, "time": f"2026-01-01T{when}:00Z",
                       "attributes": [], "relationships": links})

    event("o1", "08:00", "Place", "o1", duplicate_qualifier=True)
    event("d1", "10:00", "Ship", "d1")
    event("o2", "10:00", "Place", "o2")
    event("d2", "09:00", "Ship", "d2")       # reversed
    event("o3", "08:00", "Place", "o3")      # missing target activity
    event("d4", "09:00", "Ship", "d4")       # missing source activity
    # o5,d5 have no selected activity; still counted because relationship exists.
    event("o6", "10:00", "Place", "o6")
    event("d6", "10:00", "Ship", "d6")       # equal timestamp; not a proven handoff
    event("o7a", "08:00", "Place", "o7")
    event("o7b", "09:00", "Place", "o7")    # first occurrence chosen; repeated flagged
    event("d7", "12:00", "Ship", "d7")
    return {"objectTypes": [{"name": "order", "attributes": []},
                             {"name": "delivery", "attributes": []}],
            "eventTypes": [], "objects": objects, "events": list(reversed(events))}


def _import():
    return workspace.import_log(BytesIO(json.dumps(_document()).encode()),
                                name="O2O timings", source_format="ocel")["log_id"]


def _spec(**changes):
    return {"source_type": "order", "target_type": "delivery",
            "source_activity": "Place", "target_activity": "Ship", **changes}


def test_exact_direction_distinct_pairs_first_occurrence_missing_and_reversed():
    log = _import()
    result = leads.analyze(log, _spec())
    assert result["format"] == leads.FORMAT
    assert result["pairs"] == 7  # o1 -> d1 occurs under two qualifiers: one distinct pair
    assert result["forward_pairs"] == 3
    assert result["reversed_pairs"] == 1
    assert result["source_activity_missing_pairs"] == 1
    assert result["target_activity_missing_pairs"] == 1
    assert result["both_activities_missing_pairs"] == 1
    assert result["same_timestamp_pairs"] == 1
    assert result["pairs_with_repeated_selected_activity"] == 1
    times = result["forward_lead_time_seconds"]
    assert times["minimum"] == 0
    assert times["p50"] == pytest.approx(7200)
    assert times["p90"] == pytest.approx(12960)
    assert times["maximum"] == 14400
    assert times["mean"] == pytest.approx(7200)
    assert sum(1 for x in result["samples"] if x["status"] == "forward") == 3
    first = next(x for x in result["samples"] if x["source_object_id"] == "o1")
    assert first["source_event_occurrences"] == 1  # deduplicated event-object qualifier links
    assert first["lead_seconds"] == 7200.0
    assert result["relationship_digest"] and result["object_index_digest"]
    assert result["source_data_completeness_verified"] is False
    assert result["causal_claims"] is False
    assert result["execution_authorized"] is False
    assert result["computation_complete_for_selected_pairs"] is True


def test_qualifier_filter_is_literal_not_inferred_and_empty_result_is_unknown_not_zero():
    log = _import()
    alternate = leads.analyze(log, _spec(qualifier="alternate"))
    assert alternate["pairs"] == 1 and alternate["forward_pairs"] == 1
    assert alternate["forward_lead_time_seconds"]["p50"] == 7200
    all_ships = leads.analyze(log, _spec(qualifier="ships"))
    assert all_ships["pairs"] == 7
    nonexistent = leads.analyze(log, _spec(qualifier="not-present"))
    assert nonexistent["pairs"] == 0 and nonexistent["forward_pairs"] == 0
    assert nonexistent["forward_lead_time_seconds"]["p50"] is None
    assert nonexistent["forward_lead_time_seconds"]["mean"] is None
    assert nonexistent["sample_truncated"] is False
    # The reverse direction must NOT be guessed from co-occurrence or the supplied inverse.
    reversed_types = leads.analyze(log, _spec(source_type="delivery", target_type="order"))
    assert reversed_types["pairs"] == 0


def test_sample_budget_and_provenance_cannot_oversell_partial_rows():
    log = _import()
    res = leads.analyze(log, _spec(sample_limit=2))
    assert res["pairs"] == 7 and len(res["samples"]) == 2
    assert res["samples"][0]["status"] == "reversed"
    assert res["sample_truncated"] is True
    assert len(leads.analyze(log, _spec(sample_limit=0))["samples"]) == 0
    assert res["relationship_revision"] >= 1
    assert res["first_occurrence_only"] is True
    assert res["zero_lag_is_not_proof_of_handoff"] is True
    assert res["supplied_relationship_validity_period_known"] is False


def test_refuse_pair_explosion_before_work_and_never_return_truncated_metrics(monkeypatch):
    log = _import()
    monkeypatch.setattr(leads, "MAX_PAIRS", 3)
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most 3"):
        leads.analyze(log, _spec())


@pytest.mark.parametrize("invalid", [
    {}, [], None,
    _spec(source_type=""),
    _spec(source_type=" "),
    _spec(source_type="order\nother"),
    _spec(target_activity=12),
    _spec(sample_limit=True),
    _spec(sample_limit=-1),
    _spec(sample_limit=51),
    _spec(unknown_query="anything"),
    _spec(qualifier=4),
])
def test_invalid_or_ambiguous_queries_fail_closed(invalid):
    with pytest.raises(workspace.EventLogWorkspaceError, match="object lead times"):
        leads.parse(invalid)


def test_private_import_erasure_invalidates_analysis():
    log = _import()
    assert leads.analyze(log, _spec())["pairs"] == 7
    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown event log"):
        leads.analyze(log, _spec())


def test_actual_session_only_http_route_json_and_no_store(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = {"objectTypes": [], "eventTypes": [],
       "objects": [{"id": "o", "type": "order", "relationships":
                    [{"objectId": "d", "qualifier": "fulfills"}]},
                   {"id": "d", "type": "delivery", "relationships": []}],
       "events": [{"id": "p", "type": "Place", "time": "2026-01-01T08:00:00Z",
                   "relationships": [{"objectId": "o", "qualifier": ""}]},
                  {"id": "s", "type": "Ship", "time": "2026-01-01T09:00:00Z",
                   "relationships": [{"objectId": "d", "qualifier": ""}]}]}
spec = {"source_type": "order", "target_type": "delivery",
        "source_activity": "Place", "target_activity": "Ship"}
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    stored = {**headers, "X-OWG-Local-Storage-Confirmed": "true"}
    imp = client.post("/v1/event-logs/import?name=OCEL&format=ocel", json=doc, headers=stored)
    assert imp.status_code == 200, imp.text
    url = "/v1/event-logs/" + imp.json()["log_id"] + "/object-relationships/lead-times"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    for unauthorized in ({}, {"Authorization": "OWG-Session wrong"}, bearer):
        denied = client.post(url, json=spec, headers=unauthorized)
        assert denied.status_code in (401, 403), denied.text
    result = client.post(url, json=spec, headers=headers)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["pairs"] == 1
    assert result.json()["forward_lead_time_seconds"]["p50"] == 3600
    assert not result.json()["execution_authorized"]
    for raw in (b'{"source_type":"order","source_type":"delivery"}', b'{"sample_limit":NaN}', b'[]'):
        rejected = client.post(url, content=raw, headers={**headers, "Content-Type": "application/json"})
        assert rejected.status_code == 422, rejected.text
    assert client.post(url, content=b'{}', headers=headers).status_code == 415
    assert client.post(url, content=b' '*16385, headers={
        **headers, "Content-Type": "application/json"}).status_code == 413
    client.post("/v1/event-logs/" + imp.json()["log_id"] + "/delete", headers=headers)
    assert client.post(url, json=spec, headers=headers).status_code == 422
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "http-data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "http-auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr + result.stdout
