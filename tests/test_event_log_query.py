"""Typed ad-hoc analysis builder over saved event logs (roadmap #326 F3)."""
from __future__ import annotations

import json
from io import BytesIO

import pytest

from server import event_log_query as query
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


OCEL = {
    "objectTypes": [{"name": "order", "attributes": [{"name": "region", "type": "string"}]}],
    "eventTypes": [{"name": n, "attributes": []} for n in ("Receive", "Check", "Close")],
    "objects": [{"id": "o1", "type": "order", "attributes": [{"name": "region", "time": "1970-01-01T00:00:00Z", "value": "EU"}]},
                {"id": "o2", "type": "order", "attributes": [{"name": "region", "time": "1970-01-01T00:00:00Z", "value": "US"}]},
                {"id": "o3", "type": "order", "attributes": [{"name": "region", "time": "1970-01-01T00:00:00Z", "value": "EU"}]}],
    "events": [
        {"id": f"e{i}", "type": activity, "time": stamp, "attributes": [{"name": "channel", "value": channel}],
         "relationships": [{"objectId": order, "qualifier": ""}]}
        for i, (order, activity, stamp, channel) in enumerate([
            ("o1", "Receive", "2024-01-01T08:00:00Z", "web"), ("o1", "Check", "2024-01-01T09:00:00Z", "web"),
            ("o1", "Close", "2024-01-01T10:00:00Z", "web"),
            ("o2", "Receive", "2024-01-02T08:00:00Z", "mail"), ("o2", "Check", "2024-01-03T08:00:00Z", "mail"),
            ("o2", "Check", "2024-01-04T08:00:00Z", "mail"), ("o2", "Close", "2024-01-05T08:00:00Z", "mail"),
            ("o3", "Receive", "2024-01-09T08:00:00Z", "web"),
        ])],
}


def _log():
    return workspace.import_log(BytesIO(json.dumps(OCEL).encode()), name="q", source_format="ocel")["log_id"]


def _table(result):
    return [dict(zip(result["columns"], row)) for row in result["rows"]]


def test_case_level_group_by_attribute_with_duration_measures():
    result = query.run(_log(), {"level": "cases", "dimensions": ["attribute:region"],
                                "measures": [{"fn": "count"}, {"fn": "median", "of": "duration_seconds"},
                                             {"fn": "max", "of": "events"}]})
    assert _table(result) == [
        {"attribute:region": "EU", "count": 2, "median_duration_seconds": 3600.0, "max_events": 3},
        {"attribute:region": "US", "count": 1, "median_duration_seconds": 259200.0, "max_events": 4}]
    assert result["matching"] == {"rows": 3, "cases": 3}
    assert result["query_basis"] == "typed_spec_compiled_to_parameterized_sql"


def test_case_filters_variants_and_ordering():
    log = _log()
    closed = query.run(log, {"level": "cases", "dimensions": ["variant"], "measures": [{"fn": "count"}],
                             "where": {"ends_with": "Close"}, "order": "dimensions"})
    assert [json.loads(row[0]) for row in closed["rows"]] == [["Receive","Check","Check","Close"],["Receive","Check","Close"]]
    slow = query.run(log, {"level": "cases", "dimensions": ["has_rework"], "measures": [{"fn": "share"}],
                           "where": {"duration_over_seconds": 1800}})
    assert _table(slow) == [{"has_rework": "no rework", "share": 0.5}, {"has_rework": "rework", "share": 0.5}]
    lacking = query.run(log, {"level": "cases", "measures": [{"fn": "count"}], "where": {"lacks_activity": "Close"}})
    assert lacking["rows"] == [[1]]
    cohort = query.run(log, {"level": "cases", "measures": [{"fn": "count"}], "cohort": {"attribute": "region=US"}})
    assert cohort["rows"] == [[1]]


def test_event_level_dimensions_and_attribute_values():
    log = _log()
    by_channel = query.run(log, {"level": "events", "dimensions": ["attribute:channel", "activity"],
                                 "measures": [{"fn": "count"}, {"fn": "cases"}], "activity_in": ["Check"]})
    assert _table(by_channel) == [{"attribute:channel": "mail", "activity": "Check", "count": 2, "cases": 1},
                                  {"attribute:channel": "web", "activity": "Check", "count": 1, "cases": 1}]
    weekly = query.run(log, {"level": "events", "dimensions": ["week"], "measures": [{"fn": "count"}], "order": "dimensions"})
    assert weekly["rows"] == [["2024-01-01", 7], ["2024-01-08", 1]]
    limited = query.run(log, {"level": "events", "dimensions": ["activity"], "measures": [{"fn": "count"}], "limit": 1})
    assert limited["rows"] == [["Check", 3]] and limited["rows_truncated"] is True


@pytest.mark.parametrize("spec,message", [
    ({"level": "cases", "dimensions": ["case_id; DROP TABLE events"], "measures": [{"fn": "count"}]}, "unknown"),
    ({"level": "cases", "dimensions": ["activity"], "measures": [{"fn": "count"}]}, "unknown cases dimension"),
    ({"level": "events", "measures": [{"fn": "median", "of": "duration_seconds"}]}, "support count"),
    ({"level": "cases", "measures": [{"fn": "median"}]}, "needs a value"),
    ({"level": "cases", "measures": [{"fn": "count", "of": "events"}]}, "does not take"),
    ({"level": "cases", "measures": [{"fn": "count"}], "sql": "SELECT 1"}, "invalid"),
    ({"level": "cases", "measures": []}, "invalid"),
    ({"level": "events", "measures": [{"fn": "count"}], "where": {"min_events": 2}}, "case filters"),
    ({"level": "cases", "dimensions": ["variant", "variant"], "measures": [{"fn": "count"}]}, "different"),
    ({"level": "cases", "measures": [{"fn": "count"}], "limit": 501}, "invalid"),
])
def test_unsafe_or_malformed_specs_are_refused(spec, message):
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        query.run(_log(), spec)


def test_attribute_names_are_parameters_not_sql():
    log = _log()
    odd = query.run(log, {"level": "events", "dimensions": ["attribute:x') OR 1=1 --"], "measures": [{"fn": "count"}]})
    assert odd["rows"] == [[None, 8]]


def test_saved_analyses_are_bounded_and_deleted_with_the_log(monkeypatch):
    log, other = _log(), _log()
    spec = {"level": "cases", "dimensions": ["start_month"], "measures": [{"fn": "count"}]}
    saved = query.save(log, {"name": "Monthly cases", "spec": spec})
    assert query.list_saved(log)["analyses"][0]["name"] == "Monthly cases"
    assert query.list_saved(other)["analyses"] == []
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.save(log, {"name": "", "spec": spec})
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.save(log, {"name": "x", "spec": spec, "owner": "me"})
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.delete_saved(other, saved["analysis_id"])
    monkeypatch.setattr(query, "MAX_SAVED", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most"):
        query.save(log, {"name": "again", "spec": spec})
    workspace.delete_log(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM saved_analyses WHERE log_id = ?", [log]).fetchone()[0] == 0
    finally:
        conn.close()


def test_real_http_routes_require_a_dashboard_session(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-03T00:00:00Z\n2,A,2024-01-02T00:00:00Z\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    base = "/v1/event-logs/" + made.json()["log_id"]
    spec = {"level": "cases", "dimensions": ["last_activity"], "measures": [{"fn": "count"}], "order": "dimensions"}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(base + "/analysis", json=spec, headers=bearer).status_code in (401, 403)
    result = client.post(base + "/analysis", json=spec, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert result.json()["rows"] == [["A", 1], ["B", 1]]
    assert client.post(base + "/analysis", json={**spec, "dimensions": ["x"]}, headers=headers).status_code == 422
    saved = client.post(base + "/analyses", json={"name": "By end", "spec": spec}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert client.get(base + "/analyses", headers=headers).json()["analyses"][0]["name"] == "By end"
    gone = client.post(base + "/analyses/" + saved.json()["analysis_id"] + "/delete", headers=headers)
    assert gone.status_code == 200 and client.get(base + "/analyses", headers=headers).json()["analyses"] == []
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
