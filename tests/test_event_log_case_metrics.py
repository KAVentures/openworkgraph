"""Typed case metrics in the analysis builder and a saved metric library (#370 M5 / R9)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_query as analysis
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    rows = ["case,activity,time,amount"]
    plan = {  # case: [(activity, hours, amount)]
        "c1": [("Receive", 0, "10"), ("Approve", 2, ""), ("Pay", 5, "90.5")],
        "c2": [("Receive", 0, "20"), ("Fix", 1, ""), ("Fix", 3, ""), ("Approve", 4, ""), ("Pay", 6, "x")],
        "c3": [("Receive", 0, "5"), ("Pay", 1, "")],                          # never approved
        "c4": [("Approve", 0, ""), ("Receive", 1, "")],                       # approval before receipt
    }
    for n, (case, events) in enumerate(plan.items()):
        rows += [f"{case},{a},{(START + timedelta(days=n, hours=h)).isoformat()},{v}" for a, h, v in events]
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="m", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["amount"]})["log_id"]


WAIT = {"name": "to_approval", "kind": "time_between", "from_activity": "Receive", "to_activity": "Approve"}


def _run(log, metrics, measures, **extra):
    return analysis.run(log, {"level": "cases", "metrics": metrics, "measures": measures, **extra})


def test_time_between_discloses_missing_and_reversed_cases():
    log = _log()
    r = _run(log, [WAIT], [{"fn": "median", "of": "metric:to_approval"}, {"fn": "max", "of": "metric:to_approval"}])
    assert r["rows"] == [[3 * 3600.0, 4 * 3600.0]]                       # c1 2 h, c2 4 h
    cov = r["metrics"][0]
    assert cov["unit"] == "seconds" and cov["cases_with_value"] == 2 and cov["cases_without_value"] == 2
    assert cov["cases_without_to_activity"] == 1 and cov["cases_where_to_precedes_from"] == 1
    assert r["units"] == {"median_metric:to_approval": "seconds", "max_metric:to_approval": "seconds"}
    last = {**WAIT, "name": "to_last_fix", "to_activity": "Fix", "to_occurrence": "last"}
    first = {**last, "name": "to_first_fix", "to_occurrence": "first"}
    both = _run(log, [last, first], [{"fn": "max", "of": "metric:to_last_fix"}, {"fn": "max", "of": "metric:to_first_fix"}])
    assert both["rows"] == [[3 * 3600.0, 3600.0]]


def test_counts_and_numeric_attributes_keep_their_declared_unit():
    log = _log()
    fixes = {"name": "fixes", "kind": "activity_count", "activity": "Fix"}
    spend = {"name": "spend", "kind": "event_attribute_sum", "attribute": "amount", "unit": "EUR"}
    r = _run(log, [fixes, spend], [{"fn": "sum", "of": "metric:fixes"}, {"fn": "sum", "of": "metric:spend"},
                                   {"fn": "avg", "of": "metric:fixes"}], dimensions=["last_activity"],
             order="dimensions")
    by = {row[0]: row[1:] for row in r["rows"]}
    assert by["Pay"] == [2.0, 125.5, pytest.approx(2 / 3)]       # c1 100.5, c2 20 ("x" ignored), c3 5
    assert by["Receive"] == [0.0, None, 0.0]
    cov = {c["name"]: c for c in r["metrics"]}
    assert cov["fixes"]["cases_without_value"] == 0 and cov["spend"]["cases_with_value"] == 3
    assert r["units"]["sum_metric:spend"] == "EUR" and r["units"]["sum_metric:fixes"] == "events"


def test_metric_specs_are_validated():
    log = _log()
    for spec in (
        {"level": "cases", "metrics": [WAIT], "measures": [{"fn": "median", "of": "metric:other"}]},
        {"level": "cases", "metrics": [WAIT, WAIT], "measures": [{"fn": "count"}]},
        {"level": "events", "metrics": [WAIT], "measures": [{"fn": "count"}]},
        {"level": "cases", "metrics": [{**WAIT, "name": "Bad Name"}], "measures": [{"fn": "count"}]},
        {"level": "cases", "metrics": [{**WAIT, "kind": "sql"}], "measures": [{"fn": "count"}]},
        {"level": "cases", "metrics": [{"name": "x", "kind": "event_attribute_sum", "attribute": 'a"b', "unit": "EUR"}],
         "measures": [{"fn": "count"}]},
        {"level": "cases", "metrics": [{"name": "x", "kind": "event_attribute_sum", "attribute": "a", "unit": "<b>"}],
         "measures": [{"fn": "count"}]},
        {"level": "cases", "metrics": [WAIT], "measures": [{"fn": "count", "of": "metric:to_approval"}]},
        {"level": "cases", "metrics": [WAIT] * 5, "measures": [{"fn": "count"}]},
    ):
        with pytest.raises(workspace.EventLogWorkspaceError):
            analysis.run(log, spec)


def test_metric_library_saves_lists_and_deletes_with_the_log():
    log = _log()
    saved = analysis.save_metric(log, {"metric": WAIT})
    assert saved["format"] == analysis.METRIC_FORMAT and saved["metric"]["from_occurrence"] == "first"
    listed = analysis.list_metrics(log)["metrics"]
    assert [m["metric"]["name"] for m in listed] == ["to_approval"]
    # A saved definition runs unchanged in an analysis.
    assert _run(log, [listed[0]["metric"]], [{"fn": "median", "of": "metric:to_approval"}])["rows"] == [[3 * 3600.0]]
    for bad in ({"metric": WAIT}, {"metric": {**WAIT, "kind": "nope"}}, {"metric": WAIT, "x": 1}, {}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            analysis.save_metric(log, bad)
    assert analysis.delete_metric(log, saved["metric_id"])["deleted"] is True
    analysis.save_metric(log, {"metric": WAIT})
    workspace.delete_log(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM metric_definitions").fetchone()[0] == 0
    finally:
        conn.close()


def test_saved_analyses_keep_their_metric_definitions():
    log = _log()
    spec = {"level": "cases", "metrics": [WAIT], "measures": [{"fn": "median", "of": "metric:to_approval"}]}
    analysis.save(log, {"name": "Approval wait", "spec": spec})
    stored = analysis.list_saved(log)["analyses"][0]["spec"]
    assert stored["metrics"][0]["to_activity"] == "Approve"
    assert analysis.run(log, stored)["rows"] == [[3 * 3600.0]]


def test_real_http_routes_require_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01T01:00:00Z\n2,A,2024-01-02T00:00:00Z\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    base = "/v1/event-logs/" + made.json()["log_id"]
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    metric = {"name": "a_to_b", "kind": "time_between", "from_activity": "A", "to_activity": "B"}
    assert client.post(base + "/metrics", json={"metric": metric}, headers=bearer).status_code in (401, 403)
    saved = client.post(base + "/metrics", json={"metric": metric}, headers=headers)
    assert saved.status_code == 200 and saved.headers["cache-control"] == "no-store", saved.text
    assert client.post(base + "/metrics", json={"metric": metric}, headers=headers).status_code == 422
    spec = {"level": "cases", "metrics": [metric], "measures": [{"fn": "max", "of": "metric:a_to_b"}]}
    result = client.post(base + "/analysis", json=spec, headers=headers).json()
    assert result["rows"] == [[3600.0]] and result["metrics"][0]["cases_without_to_activity"] == 1
    listed = client.get(base + "/metrics", headers=headers).json()["metrics"]
    assert client.post(base + "/metrics/" + listed[0]["metric_id"] + "/delete", headers=headers).status_code == 200
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
