"""R9 case-level numeric event-attribute statistics: real log/API/definition acceptance."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_query as query
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
DOC = (
    "case,activity,time,amount\n"
    "a,Receive,2024-01-01T00:00:00Z,10\n"
    "a,Pay,2024-01-01T01:00:00Z,20\n"
    "b,Receive,2024-01-02T00:00:00Z,5\n"
    "b,Pay,2024-01-02T01:00:00Z,invalid\n"
    "c,Receive,2024-01-03T00:00:00Z,\n"
    "c,Pay,2024-01-03T01:00:00Z,NaN\n"
    "d,Receive,2024-01-04T00:00:00Z,1e308\n"
    "d,Pay,2024-01-04T01:00:00Z,1e308\n"
).encode()
COHORT = {"started_before": "2024-01-04T00:00:00Z"}


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    return workspace.import_log(BytesIO(DOC), name="stats", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["amount"]})["log_id"]


def metric(statistic="average", missing_policy="available"):
    return {"name": "price", "kind": "event_attribute_statistic", "attribute": "amount",
            "unit": "EUR", "statistic": statistic, "missing_policy": missing_policy}


def run(log, stat="average", policy="available", **kw):
    return query.run(log, {"level": "cases", "cohort": COHORT,
        "metrics": [metric(stat, policy)],
        "measures": [{"fn": "sum", "of": "metric:price"}], **kw})


@pytest.mark.parametrize(("statistic", "expected"), [
    ("average", 20.0), ("minimum", 15.0), ("maximum", 25.0),
    ("median", 20.0), ("p90", 24.0)])
def test_available_statistics_use_only_finite_numeric_events(log, statistic, expected):
    result = run(log, statistic)
    assert result["rows"] == [[expected]]
    assert result["matching"]["cases"] == 3
    coverage = result["metrics"][0]
    assert coverage["cases_with_value"] == 2
    assert coverage["cases_without_value"] == 1
    assert coverage["flattened_case_event_rows_considered"] == 6
    assert coverage["event_rows_with_finite_attribute"] == 3
    assert coverage["event_rows_with_missing_invalid_or_nonfinite_attribute"] == 3
    assert coverage["cases_with_partial_attribute_coverage"] == 1
    assert coverage["cases_with_no_finite_attribute"] == 1
    assert coverage["cases_excluded_by_complete_policy"] == 0
    assert coverage["coverage_scope"] == "selected_full_cases_flattened_case_event_rows"
    assert result["units"] == {"sum_metric:price": "EUR"}
    json.dumps(result, allow_nan=False)


def test_complete_policy_withholds_partially_observed_case(log):
    result = run(log, "average", "complete")
    assert result["rows"] == [[15.0]]
    coverage = result["metrics"][0]
    assert coverage["cases_with_value"] == 1
    assert coverage["cases_without_value"] == 2
    assert coverage["cases_excluded_by_complete_policy"] == 1
    assert coverage["definition"]["missing_policy"] == "complete"
    assert run(log, "maximum", "complete")["rows"] == [[20.0]]
    assert run(log, "median", "complete")["rows"] == [[15.0]]
    assert run(log, "p90", "complete")["rows"] == [[19.0]]


@pytest.mark.parametrize(("statistic", "expected"), [
    ("median", {"OddStart": 3.0, "EvenStart": 6.0, "MissingStart": 5.0}),
    ("p90", {"OddStart": 7.8, "EvenStart": 9.4, "MissingStart": 5.0}),
])
def test_distribution_quantiles_interpolate_odd_even_and_skip_nonfinite(log, statistic, expected):
    # The imported values remain unchanged; only finite observations enter each
    # case's continuous quantile, and missingness is still disclosed.
    doc = (
        "case,activity,time,amount\n"
        "odd,OddStart,2024-02-01T00:00:00Z,1\n"
        "odd,OddEnd,2024-02-01T01:00:00Z,3\n"
        "odd,OddEnd,2024-02-01T02:00:00Z,9\n"
        "even,EvenStart,2024-02-02T00:00:00Z,2\n"
        "even,EvenEnd,2024-02-02T01:00:00Z,4\n"
        "even,EvenEnd,2024-02-02T02:00:00Z,8\n"
        "even,EvenEnd,2024-02-02T03:00:00Z,10\n"
        "missing,MissingStart,2024-02-03T00:00:00Z,5\n"
        "missing,MissingEnd,2024-02-03T01:00:00Z,NaN\n"
    ).encode()
    new_log = workspace.import_log(BytesIO(doc), name="quantiles", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["amount"]})["log_id"]
    def analyze(policy):
        return query.run(new_log, {"level": "cases", "dimensions": ["first_activity"],
            "metrics": [metric(statistic, policy)],
            "measures": [{"fn": "avg", "of": "metric:price"}]})
    available = analyze("available")
    assert dict(available["rows"]) == expected
    assert available["metrics"][0]["event_rows_with_finite_attribute"] == 8
    assert available["metrics"][0]["event_rows_with_missing_invalid_or_nonfinite_attribute"] == 1
    assert available["metrics"][0]["cases_with_partial_attribute_coverage"] == 1
    complete = analyze("complete")
    assert dict(complete["rows"]) == {**expected, "MissingStart": None}
    assert complete["metrics"][0]["cases_excluded_by_complete_policy"] == 1
    assert complete["metrics"][0]["cases_with_value"] == 2
    json.dumps(available, allow_nan=False)
    json.dumps(complete, allow_nan=False)


def test_nonfinite_inputs_do_not_poison_extrema_and_empty_cohort_is_unknown(log):
    all_cases = query.run(log, {"level": "cases", "metrics": [metric("maximum")],
        "measures": [{"fn": "max", "of": "metric:price"}]})
    assert all_cases["rows"] == [[1e308]]
    assert all_cases["metrics"][0]["cases_with_value"] == 3
    empty = run(log, where={"ends_with": "NotPresent"})
    assert empty["rows"] == [[None]]
    assert empty["metrics"][0]["flattened_case_event_rows_considered"] == 0
    assert empty["metrics"][0]["cases_with_value"] == 0
    json.dumps(empty, allow_nan=False)


@pytest.mark.parametrize(("statistic", "expected"), [
    ("average", 24.0), ("median", 24.0), ("p90", 28.0)])
def test_statistic_can_feed_typed_arithmetic_and_saved_analyses(log, statistic, expected):
    derived = {"name": "plus_two", "kind": "arithmetic", "operation": "add",
        "left": {"kind": "metric", "name": "price"},
        "right": {"kind": "constant", "value": 2.0, "unit": "EUR"}}
    spec = {"level": "cases", "cohort": COHORT, "metrics": [metric(statistic), derived],
        "measures": [{"fn": "sum", "of": "metric:plus_two"}]}
    assert query.run(log, spec)["rows"] == [[expected]]
    template = query.save_metric(log, {"metric": metric(statistic)})
    saved = query.save(log, {"name": "Observed event price", "spec": spec})
    assert query.list_metrics(log)["metrics"][0]["metric"] == template["metric"]
    query.delete_metric(log, template["metric_id"])
    restored = query.list_saved(log)["analyses"][0]
    assert restored["spec"] == saved["spec"]
    assert query.run(log, restored["spec"])["rows"] == [[expected]]


@pytest.mark.parametrize("bad", [
    {"statistic": "p95"}, {"missing_policy": "assume_zero"},
    {"unit": "USD\x00"}, {"attribute": 'amount" OR 1=1 --'},
    {"attribute": ""}, {"statistic": None}, {"sql": "DROP TABLE events"},
])
def test_invalid_contract_refused_before_query(monkeypatch, bad):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid metric accessed the dataset")
    monkeypatch.setattr(workspace, "_query", forbidden)
    spec = {"level": "cases", "metrics": [{**metric(), **bad}],
            "measures": [{"fn": "avg", "of": "metric:price"}]}
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.run("unused", spec)


def test_event_level_metric_refused_before_dataset_access(monkeypatch):
    monkeypatch.setattr(workspace, "_query", lambda *a, **kw: pytest.fail("dataset accessed"))
    with pytest.raises(workspace.EventLogWorkspaceError, match="case metrics"):
        query.run("unused", {"level": "events", "metrics": [metric()], "measures": [{"fn": "count"}]})


def test_authenticated_http_analysis_save_and_denial(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time,amount\na,A,2024-01-01T00:00:00Z,10\na,B,2024-01-01T01:00:00Z,20\nb,A,2024-01-02T00:00:00Z,5\nb,B,2024-01-02T01:00:00Z,invalid\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=stats&case_column=case&activity_column=activity&timestamp_column=time&attribute_column=amount",
        content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    base = "/v1/event-logs/" + made.json()["log_id"]
    m = {"name": "avg_amount", "kind": "event_attribute_statistic", "attribute": "amount",
         "unit": "EUR", "statistic": "average", "missing_policy": "complete"}
    spec = {"level": "cases", "metrics": [m], "measures": [{"fn": "sum", "of": "metric:avg_amount"}]}
    assert client.post(base + "/analysis", json=spec).status_code in (401, 403)
    assert client.post(base + "/analysis", json=spec, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    r = client.post(base + "/analysis", json=spec, headers=headers)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    assert r.json()["rows"] == [[15.0]]
    assert r.json()["metrics"][0]["cases_excluded_by_complete_policy"] == 1
    for statistic, expected in (("median", 15.0), ("p90", 19.0)):
        other = client.post(base + "/analysis", json={**spec, "metrics": [{**m, "statistic": statistic}]}, headers=headers)
        assert other.status_code == 200, other.text
        assert other.json()["rows"] == [[expected]]
        assert other.json()["metrics"][0]["definition"]["statistic"] == statistic
    assert client.post(base + "/metrics", json={"metric": m}, headers=headers).status_code == 200
    saved = client.post(base + "/analyses", json={"name": "Verified contract", "spec": spec}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert client.get(base + "/analyses", headers=headers).json()["analyses"][0]["spec"] == saved.json()["spec"]
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
