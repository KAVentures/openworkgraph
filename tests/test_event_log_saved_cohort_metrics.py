"""R6 saved cohort expression integration: authorization, log isolation, deletion."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import event_log_routes
from server import event_log_saved_cohort_metrics as saved
from server import event_log_workspace as workspace


def test_rejects_unapproved_fields_and_malformed_definitions():
    valid = {"name": "Cycle time", "definition": {"version": 1, "expression": {"field": "duration_seconds"}}}
    assert saved._validated(valid)[0] == "Cycle time"
    for bad in (
        {"name": "Leak", "definition": {"version": 1, "expression": {"field": "case_id"}}},
        {"name": "", "definition": valid["definition"]},
        {"name": "x", "definition": {"version": 1, "expression": {"literal": float("nan")}}},
        {"name": "x", "definition": valid["definition"], "cohort": {"resource": "secret"}},
    ):
        with pytest.raises(workspace.EventLogWorkspaceError):
            saved._validated(bad)


def test_saved_definition_lifecycle_and_log_isolation(monkeypatch, tmp_path):
    import duckdb
    db = tmp_path / "cohort.duckdb"
    def connect():
        conn = duckdb.connect(str(db))
        conn.execute(workspace._SCHEMA)
        return conn
    monkeypatch.setattr(workspace, "_connect", connect)
    with connect() as conn:
        for log_id in ("log_0123456789abcdef", "log_1111111111111111"):
            conn.execute("INSERT INTO logs VALUES (?, ?, ?, current_timestamp, 0, 0, 0)", [log_id, "test", "csv"])
    first, second = "log_0123456789abcdef", "log_1111111111111111"
    body = {"name": "Events", "definition": {"version": 1, "expression": {"field": "event_count"}}}
    result = saved.save(first, body)
    assert saved.list_saved(first)["definitions"][0]["metric_id"] == result["metric_id"]
    assert saved.list_saved(second)["definitions"] == []
    with pytest.raises(workspace.EventLogWorkspaceError):
        saved.delete(second, result["metric_id"])
    with pytest.raises(workspace.EventLogWorkspaceError):
        saved.save(first, body)
    assert saved.delete(first, result["metric_id"])["deleted"]
    assert saved.list_saved(first)["definitions"] == []


def test_routes_require_dashboard_session_before_mutating(monkeypatch):
    monkeypatch.setattr(event_log_routes, "dashboard_session_valid", lambda token: token == "valid")
    app = FastAPI()
    app.include_router(event_log_routes.router)
    client = TestClient(app)
    path = "/v1/event-logs/log_0123456789abcdef/cohort-metric-definitions"
    assert client.get(path).status_code == 403
    assert client.post(path, json={"name": "x", "definition": {}}).status_code == 403
    assert client.post(path + "/cm_0123456789abcdef/delete").status_code == 403


def test_saved_cohort_dashboard_has_save_load_delete_controls():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "dashboard" / "event_log_workspace.js").read_text(encoding="utf-8")
    for marker in ("owgCohortMetricSaved", "owgCohortMetricSave", "owgCohortMetricDelete",
                   "cohort-metric-definitions", "currentDefinition"):
        assert marker in source
