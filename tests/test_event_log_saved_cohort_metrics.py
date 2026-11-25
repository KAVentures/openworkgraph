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


def test_saved_metric_evaluation_is_log_scoped_and_rejects_deleted(monkeypatch, tmp_path):
    import duckdb
    db = tmp_path / "saved-eval.duckdb"
    def connect():
        conn = duckdb.connect(str(db))
        conn.execute(workspace._SCHEMA)
        return conn
    monkeypatch.setattr(workspace, "_connect", connect)
    first, other = "log_0123456789abcdef", "log_1111111111111111"
    with connect() as conn:
        for log_id in (first, other):
            conn.execute("INSERT INTO logs VALUES (?, ?, ?, current_timestamp, 0, 0, 0)",
                         [log_id, "test", "csv"])
    item = saved.save(first, {
        "name": "Events", "definition": {"version": 1, "expression": {"field": "event_count"}}
    })
    calls = []
    def summarize(log_id, spec, case_type=None, *, include_case_values=False):
        calls.append((log_id, spec, case_type, include_case_values))
        return {"metric": {"sum": "2"}, "raw_cases_returned": False}
    monkeypatch.setattr(saved.cohort_metrics, "summarize", summarize)
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown"):
        saved.evaluate(other, item["metric_id"], {})
    assert not calls
    result = saved.evaluate(first, item["metric_id"], {"activity": "Review"}, "case", include_case_values=True)
    assert result["saved_metric"] == {"metric_id": item["metric_id"], "name": "Events",
                                      "definition_sha256": item["definition_sha256"]}
    assert len(result["saved_metric"]["definition_sha256"]) == 64
    assert calls[0][1]["cohort"] == {"activity": "Review"}
    assert calls[0][2:] == ("case", True)
    saved.delete(first, item["metric_id"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown"):
        saved.evaluate(first, item["metric_id"], {})
    assert len(calls) == 1


def test_saved_metric_evaluation_api_requires_session_and_rechecks_revocation(monkeypatch):
    monkeypatch.setattr(event_log_routes, "dashboard_session_valid", lambda token: token == "valid")
    app = FastAPI()
    app.include_router(event_log_routes.router)
    client = TestClient(app)
    path = "/v1/event-logs/log_0123456789abcdef/cohort-metric-definitions/cm_0123456789abcdef/evaluate"
    assert client.post(path, json={"cohort": {}}).status_code == 403
    headers = {"Authorization": "OWG-Session valid"}
    assert client.post(path, headers=headers, json={"definition": {}, "cohort": {}}).status_code == 422
    def revoke(*args, **kwargs):
        monkeypatch.setattr(event_log_routes, "dashboard_session_valid", lambda token: False)
        return {"metric": {"sum": "1"}}
    monkeypatch.setattr(saved, "evaluate", revoke)
    assert client.post(path + "?include_case_values=true", headers=headers, json={"cohort": {}}).status_code == 403


def test_dashboard_evaluates_saved_revision_and_clears_it_on_manual_edit():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "dashboard" / "event_log_workspace.js").read_text(encoding="utf-8")
    assert "'/evaluate':'/cohort-metric'" in source
    assert "const payload=savedId?{cohort}:{definition:currentDefinition(),cohort};" in source
    assert "control.addEventListener('change',()=>{savedPicker.value='';});" in source


def test_saved_metric_digest_is_stable_and_sensitive_to_expression():
    first = {"version": 1, "expression": {"op": "add", "args": [{"field": "event_count"}, {"literal": 2}]}}
    reordered = {"expression": {"args": [{"field": "event_count"}, {"literal": 2}], "op": "add"}, "version": 1}
    changed = {"version": 1, "expression": {"field": "event_count"}}
    assert saved._definition_digest(first) == saved._definition_digest(reordered)
    assert saved._definition_digest(first) != saved._definition_digest(changed)


def test_dashboard_rejects_saved_revision_drift_and_shows_verified_digest():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "dashboard" / "event_log_workspace.js").read_text(encoding="utf-8")
    assert "data.saved_metric.definition_sha256!==selected.definition_sha256" in source
    assert "data.saved_metric.metric_id!==savedId" in source
    assert "Saved metric revision changed. Reload saved expressions and calculate again." in source
    assert "Saved definition SHA-256: " in source
    assert "await loadSaved();" in source
