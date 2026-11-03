"""R9 linked business-attribute analytics: actual OCEL storage, HTTP auth and ambiguity."""
from __future__ import annotations

from io import BytesIO
import json

import pytest

from server import event_log_linked_attribute_analysis as joined
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def private_log_store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _doc():
    initial = "1970-01-01T00:00:00Z"
    orders = [{"id": f"o{i}", "type": "order", "attributes": []} for i in range(1, 9)]
    values = {"i1": "EU", "i2": "US", "i3": "EU", "i5": "EU",
              "i6": "X" * 121, "i7": "EU", "i8": "EU", "i9": "US"}
    invoices = [{"id": f"i{i}", "type": "invoice",
                 "attributes": ([{"name": "region", "time": initial, "value": values[f"i{i}"]}]
                                if f"i{i}" in values else [])}
                for i in range(1, 10)]
    links = [
        ("o1", ["i1"]), ("o1", ["i1"]),
        ("o2", ["i2"]), ("o2", ["i3"]),
        ("o3", []), ("o4", ["i4"]), ("o5", ["i5"]),
        ("o6", ["i6"]), ("o7", ["i7", "i8"]), ("o8", ["i9"]),
    ]
    times = ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z",
             "2024-01-02T00:00:00Z", "2024-01-02T01:00:00Z",
             "2024-01-03T00:00:00Z", "2024-01-04T00:00:00Z",
             "2024-01-05T00:00:00Z", "2024-01-06T00:00:00Z",
             "2024-01-07T00:00:00Z", "2024-01-08T00:00:00Z"]
    events = [{"id": f"e{i}", "type": "Work", "time": time, "attributes": [],
               "relationships": [{"objectId": case, "qualifier": ""}] +
                                [{"objectId": inv, "qualifier": ""} for inv in related]}
              for i, ((case, related), time) in enumerate(zip(links, times))]
    return {"objectTypes": [{"name": "order", "attributes": []},
                             {"name": "invoice", "attributes": [{"name": "region", "type": "string"}]}],
            "eventTypes": [{"name": "Work", "attributes": []}],
            "objects": orders + invoices, "events": events}


def _import(doc=None):
    return workspace.import_log(BytesIO(json.dumps(doc or _doc()).encode()),
                                name="joined", source_format="ocel")["log_id"]


def _spec(**changes):
    return {"case_type": "order", "related_object_type": "invoice",
            "attribute": "region", **changes}


def test_real_log_join_deduplicates_events_and_reports_unknown_without_inventing_values():
    log = _import()
    conn = workspace._connect()
    try:
        # Duplicate event/object links cannot multiply the resolved case count.
        conn.execute("INSERT INTO event_objects VALUES (?, 'e0', 'i1', '')", [log])
    finally:
        conn.close()
    result = joined.analyze(log, _spec())
    assert result["format"] == joined.FORMAT
    assert result["cases"] == 8
    assert result["status_counts"] == {
        "resolved": 3, "unlinked": 1, "ambiguous": 2,
        "missing_attribute": 1, "oversize_attribute": 1,
    }
    assert result["groups_total"] == 2
    assert result["rows"] == [
        {"value": "EU", "cases": 2, "median_duration_seconds": 1800.0},
        {"value": "US", "cases": 1, "median_duration_seconds": 0.0},
    ]
    assert result["case_notion"] == "order"
    assert result["join_basis"] == "distinct_same_log_coobserved_event_object_links"
    assert not result["verified_business_workflow"]
    packet = json.dumps(result, allow_nan=False)
    assert '"o1"' not in packet and '"i1"' not in packet
    assert "X" * 121 not in packet


def test_bounded_group_pagination_discloses_omitted_cases():
    result = joined.analyze(_import(), _spec(limit=1))
    assert result["groups_total"] == 2
    assert result["groups_returned"] == 1
    assert result["groups_omitted"] == 1
    assert result["resolved_cases_omitted_by_group_limit"] == 1
    assert result["rows"][0]["value"] == "EU"


def test_cohort_applies_to_full_cases_before_join_and_denominators():
    result = joined.analyze(_import(), _spec(
        cohort={"started_before": "2024-01-03T00:00:00Z"}))
    assert result["cases"] == 2
    assert result["cases_before_filter"] == 8
    assert result["status_counts"]["resolved"] == 1
    assert result["status_counts"]["ambiguous"] == 1
    assert result["rows"][0]["median_duration_seconds"] == 3600.0


def test_conflicting_stored_object_definitions_refuse_resolution():
    log = _import()
    conn = workspace._connect()
    try:
        conn.execute("INSERT INTO objects VALUES (?, 'i1', 'invoice', ?)",
                     [log, '{"region":"CONFLICT"}'])
    finally:
        conn.close()
    result = joined.analyze(log, _spec())
    assert result["status_counts"]["resolved"] == 2
    assert result["status_counts"]["ambiguous"] == 3


def test_other_logs_cannot_supply_joined_objects():
    log = _import()
    second = _doc()
    next(obj for obj in second["objects"] if obj["id"] == "i1")["attributes"][0]["value"] = "OTHER"
    _import(second)
    result = joined.analyze(log, _spec())
    assert result["status_counts"]["resolved"] == 3
    assert result["status_counts"]["ambiguous"] == 2


@pytest.mark.parametrize("changes", [
    {"related_object_type": "order"},
    {"related_object_type": "unknown"},
    {"attribute": 'region" OR 1=1 --'},
    {"attribute": ""},
    {"limit": 0},
    {"limit": 101},
    {"limit": True},
    {"extra": "not supported"},
    {"cohort": {"started_before": "not a date"}},
])
def test_invalid_or_unsupported_joins_fail_closed(changes):
    log = _import()
    with pytest.raises(workspace.EventLogWorkspaceError):
        joined.analyze(log, _spec(**changes))


def test_work_budget_refuses_before_join(monkeypatch):
    log = _import()
    monkeypatch.setattr(joined, "MAX_CASE_EVENT_ROWS", 0)
    with pytest.raises(workspace.EventLogWorkspaceError, match="work budget"):
        joined.analyze(log, _spec())


def test_authenticated_rest_requires_session_and_preserves_no_store():
    from fastapi.testclient import TestClient
    from server.secure_app import app
    from server import local_auth

    log = _import()
    endpoint = f"/v1/event-logs/{log}/linked-attribute-analysis"
    with TestClient(app) as client:
        for headers in ({}, {"Authorization": "OWG-Session wrong"},
                        {"Authorization": "Bearer " + local_auth.ensure_api_token()}):
            response = client.post(endpoint, json=_spec(), headers=headers)
            assert response.status_code in (401, 403), response.text
        headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
        result = client.post(endpoint, json=_spec(), headers=headers)
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["status_counts"]["resolved"] == 3
        assert client.post(endpoint, json=_spec(limit=0), headers=headers).status_code == 422
        assert client.post(endpoint, content=b'{"attribute":"region","attribute":"region"}',
                           headers={**headers, "content-type": "application/json"}).status_code == 422
        assert client.post(endpoint, json=_spec(),
                           headers={**headers, "content-type": "text/plain"}).status_code == 415
