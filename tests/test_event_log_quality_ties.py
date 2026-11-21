from io import BytesIO

import pytest

from server import event_log_quality_ties as ties
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_timestamp_ties_are_counted_per_case_and_activity():
    data = b'''case,activity,time
1,A,2024-01-01T00:00:00Z
1,B,2024-01-01T00:00:00Z
1,A,2024-01-01T01:00:00Z
2,A,2024-01-01T00:00:00Z
2,C,2024-01-01T02:00:00Z
'''
    log = workspace.import_log(BytesIO(data), name="ties", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    result = ties.timestamp_ties(log)
    assert (result["case_event_occurrences"], result["tied_case_event_occurrences"]) == (5, 2)
    assert (result["cases"], result["cases_with_ties"]) == (2, 1)
    assert (result["tied_timestamp_groups"], result["groups_with_different_activities"],
            result["cases_with_different_activities_tied"]) == (1, 1, 1)
    assert result["activities"] == [
        {"activity": "A", "occurrences": 3, "tied_occurrences": 1, "cases_with_ties": 1},
        {"activity": "B", "occurrences": 1, "tied_occurrences": 1, "cases_with_ties": 1},
        {"activity": "C", "occurrences": 1, "tied_occurrences": 0, "cases_with_ties": 0},
    ]
    assert not result["activities_truncated"]


def test_same_activity_ties_are_distinguished_from_mixed_groups():
    data = b'''case,activity,time,resource
1,A,2024-01-01T00:00:00Z,one
1,A,2024-01-01T00:00:00Z,two
1,B,2024-01-01T01:00:00Z,one
1,C,2024-01-01T01:00:00Z,one
'''
    log = workspace.import_log(BytesIO(data), name="ties", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "resource"})["log_id"]
    result = ties.timestamp_ties(log)
    assert result["tied_timestamp_groups"] == 2
    assert result["groups_with_different_activities"] == 1
    assert result["cases_with_different_activities_tied"] == 1


def test_filter_and_dashboard_authorization():
    from fastapi.testclient import TestClient
    from server import local_auth
    from server.secure_app import app

    data = b'''case,activity,time
1,A,2024-01-01T00:00:00Z
1,B,2024-01-01T00:00:00Z
2,C,2024-01-02T00:00:00Z
'''
    log = workspace.import_log(BytesIO(data), name="ties", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    filtered = ties.timestamp_ties(log, cohort={"activity": "C"})
    assert filtered["cases"] == 1 and filtered["cases_before_filter"] == 2
    assert filtered["tied_case_event_occurrences"] == 0
    url = f"/v1/event-logs/{log}/quality/timestamp-ties"
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    with TestClient(app) as client:
        assert client.get(url).status_code in (401, 403)
        assert client.get(url, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
        response = client.get(url, headers=headers)
        assert response.status_code == 200 and response.json()["cases_with_ties"] == 1
        assert response.headers["cache-control"] == "no-store"
