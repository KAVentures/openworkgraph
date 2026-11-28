"""Actual retained report integration for the conditional uncertainty view."""
import pytest

from server import automation_monitoring as m
from server import automation_run_concurrency as concurrency
from test_automation_run_evidence import store, imported, report
from test_automation_run_concurrency import HASH, interval, settings


def test_conditional_bounds_and_existing_measured_views():
    dep = imported([
        interval("measured", "2026-01-02T00:00:00Z", "2026-01-02T00:00:04Z"),
        interval("open", "2026-01-02T00:00:02Z", None, "running"),
        interval("zero", "2026-01-02T00:00:01Z", "2026-01-02T00:00:01Z"),
    ], HASH)
    spec = settings(view="uncertainty", limit=1)
    first = concurrency.review(dep, spec)
    assert first["uncertainty_scope"] == "selected_retained_records_only"
    s = first["uncertainty_summary"]
    assert s["measured_intervals"] == s["unresolved_records"] == 1
    assert s["peak_lower_runs"] == 1 and s["peak_upper_runs"] == 2
    assert s["measured_above_capacity_us"] == 0
    assert s["additional_possible_above_capacity_us"] == 2000000
    assert s["upper_above_capacity_us"] == 2000000
    assert s["not_above_in_envelope_us"] == 86398000000
    assert s["lower_run_us"] == "4000000"
    assert s["upper_run_us"] == "86402000000"
    rows = list(first["rows"])
    page = first
    while page["next_offset"] is not None:
        page = concurrency.review(dep, {**spec, "offset": page["next_offset"],
                                       "expected_review_sha256": first["review_sha256"]})
        rows.extend(page["rows"])
    assert [(r["start_us"], r["end_us"], r["lower_runs"], r["upper_runs"]) for r in rows] == [
        (0, 2000000, 1, 1), (2000000, 4000000, 1, 2), (4000000, 86400000000, 0, 1)]
    old = concurrency.review(dep, settings(view="segments"))
    assert "uncertainty_summary" not in old
    assert old["summary"] == first["summary"]
    assert old["histogram"] == first["histogram"]
    assert not first["live_concurrency_verified"] and not first["execution_authorized"]
    with pytest.raises(m.MonitoringError, match="changed"):
        concurrency.review(dep, {**spec, "capacity": 2, "expected_review_sha256": first["review_sha256"]})
    m.import_report(report([interval("new", "2026-01-02T00:00:03Z", "2026-01-02T00:00:05Z")], HASH), dep)
    with pytest.raises(m.MonitoringError, match="changed"):
        concurrency.review(dep, {**spec, "expected_review_sha256": first["review_sha256"]})


def test_authenticated_uncertainty_view(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import automation_monitoring_routes as routes
    dep = imported([interval("open", "2026-01-02T00:00:00Z", None, "running")], HASH)
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "test")
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        url = "/v1/automation-monitoring/deployments/"+dep+"/run-concurrency"
        spec = settings(view="uncertainty")
        assert client.post(url, json=spec).status_code == 403
        assert client.post(url, json=spec, headers={"Authorization":"Bearer test"}).status_code == 403
        result = client.post(url, json=spec, headers={"Authorization":"OWG-Session test"})
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["uncertainty_summary"]["peak_upper_runs"] == 1
