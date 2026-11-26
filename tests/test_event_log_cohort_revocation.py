"""R6 saved-log numeric metrics must not outlive dashboard authorization."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import event_log_routes, event_log_cohort_metrics


PATH = "/v1/event-logs/log_0123456789abcdef/cohort-metric"
REQUEST = {
    "definition": {"version": 1, "expression": {"field": "event_count"}},
    "cohort": {},
}


@pytest.mark.parametrize("include_case_values", [False, True])
def test_cohort_metric_revoked_during_calculation_is_not_released(monkeypatch, include_case_values):
    active = {"valid": True}
    monkeypatch.setattr(
        event_log_routes, "dashboard_session_valid",
        lambda token: token == "valid" and active["valid"],
    )

    def revoke_during_query(*args, **kwargs):
        assert kwargs["include_case_values"] is include_case_values
        active["valid"] = False
        return {"metric": {"mean": "999.0"}, "raw_cases_returned": False}

    monkeypatch.setattr(event_log_cohort_metrics, "summarize", revoke_during_query)
    app = FastAPI()
    app.include_router(event_log_routes.router)
    client = TestClient(app)

    denied = client.post(PATH, json=REQUEST)
    assert denied.status_code == 403

    revoked = client.post(
        PATH, json=REQUEST, params={"include_case_values": include_case_values},
        headers={"Authorization": "OWG-Session valid"}
    )
    assert revoked.status_code == 403
    assert "999.0" not in revoked.text


def test_cohort_metric_keeps_no_store_when_session_remains_valid(monkeypatch):
    monkeypatch.setattr(
        event_log_routes, "dashboard_session_valid", lambda token: token == "valid"
    )
    monkeypatch.setattr(
        event_log_cohort_metrics,
        "summarize",
        lambda *args, include_case_values=False: {"metric": {"mean": "2"}, "raw_cases_returned": False},
    )
    app = FastAPI()
    app.include_router(event_log_routes.router)
    client = TestClient(app)
    response = client.post(
        PATH, json=REQUEST, headers={"Authorization": "OWG-Session valid"}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["metric"]["mean"] == "2"
