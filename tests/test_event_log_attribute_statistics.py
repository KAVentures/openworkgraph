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


def test_activity_scoped_statistics_distinguish_no_activity_from_invalid_values(log):
    doc = (
        "case,activity,time,amount\n"
        "x,Receive,2024-02-01T00:00:00Z,2\n"
        "x,Pay,2024-02-01T01:00:00Z,6\n"
        "y,Receive,2024-02-02T00:00:00Z,3\n"
        "z,Pay,2024-02-03T00:00:00Z,invalid\n"
        "w,Pay,2024-02-04T00:00:00Z,9\n"
        "w,Pay,2024-02-04T01:00:00Z,NaN\n"
        "r,Refund,2024-02-05T00:00:00Z,11\n"
    ).encode()
    selected = workspace.import_log(BytesIO(doc), name="activity-scope", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["amount"]})["log_id"]

    def scoped(activity, policy="available", statistic="average", **kw):
        return query.run(selected, {"level": "cases", "metrics": [{
            **metric(statistic, policy), "activity": activity}],
            "measures": [{"fn": "sum", "of": "metric:price"}], **kw})

    pay = scoped("Pay")
    assert pay["rows"] == [[15.0]]
    assert pay["matching"]["cases"] == 5
    coverage = pay["metrics"][0]
    assert coverage["flattened_case_event_rows_considered"] == 4
    assert coverage["event_rows_with_finite_attribute"] == 2
    assert coverage["event_rows_with_missing_invalid_or_nonfinite_attribute"] == 2
    assert coverage["cases_with_partial_attribute_coverage"] == 1
    assert coverage["cases_without_matching_activity"] == 2
    assert coverage["cases_with_no_finite_attribute"] == 3
    assert coverage["cases_with_value"] == 2
    assert coverage["cases_without_value"] == 3
    assert coverage["coverage_scope"] == "selected_full_cases_matching_activity_flattened_case_event_rows"
    assert pay["units"] == {"sum_metric:price": "EUR"}
    assert scoped("Pay", "complete")["rows"] == [[6.0]]
    assert scoped("Pay", statistic="median")["rows"] == [[15.0]]
    assert scoped("Refund")["rows"] == [[11.0]]
    assert scoped("NotObserved")["rows"] == [[None]]
    assert scoped("NotObserved")["metrics"][0]["cases_without_matching_activity"] == 5
    assert scoped("Pay' OR 1=1 --")["rows"] == [[None]]  # literal, bound; never SQL
    filtered = scoped("Pay", where={"ends_with": "Pay"})
    assert filtered["matching"]["cases"] == 3
    assert filtered["metrics"][0]["cases_without_matching_activity"] == 0
    assert filtered["metrics"][0]["cases_with_no_finite_attribute"] == 1
    json.dumps(pay, allow_nan=False)

    pay_metric = {**metric(), "name": "paid", "activity": "Pay"}
    received_metric = {**metric(), "name": "received", "activity": "Receive"}
    difference = {"name": "net", "kind": "arithmetic", "operation": "subtract",
                  "left": {"kind": "metric", "name": "paid"},
                  "right": {"kind": "metric", "name": "received"}}
    spec = {"level": "cases", "metrics": [pay_metric, received_metric, difference],
            "measures": [{"fn": "sum", "of": "metric:net"}]}
    assert query.run(selected, spec)["rows"] == [[4.0]]
    template = query.save_metric(selected, {"metric": pay_metric})
    assert query.list_metrics(selected)["metrics"][0]["metric"]["activity"] == "Pay"
    saved = query.save(selected, {"name": "Pay less Receive", "spec": spec})
    assert query.run(selected, query.list_saved(selected)["analyses"][0]["spec"])["rows"] == [[4.0]]
    assert saved["spec"]["metrics"][0]["activity"] == "Pay"
    query.delete_metric(selected, template["metric_id"])


@pytest.mark.parametrize("activity", ["", "a" * 201, 123, False])
def test_invalid_activity_selector_refused_before_dataset_access(monkeypatch, activity):
    monkeypatch.setattr(workspace, "_query", lambda *a, **kw: pytest.fail("dataset accessed"))
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.run("unused", {"level": "cases",
            "metrics": [{**metric(), "activity": activity}],
            "measures": [{"fn": "sum", "of": "metric:price"}]})



def test_stage_bounded_statistics_use_ordered_complete_intervals_and_missingness(log):
    # A prior End does not close a later Start; only the first later End does.
    doc = (
        "case,activity,time,amount\n"
        "a,Start,2024-03-01T00:00:00Z,2\n"
        "a,Work,2024-03-01T01:00:00Z,4\n"
        "a,End,2024-03-01T02:00:00Z,100\n"
        "b,End,2024-03-02T00:00:00Z,999\n"
        "b,Start,2024-03-02T01:00:00Z,3\n"
        "b,Work,2024-03-02T02:00:00Z,5\n"
        "b,End,2024-03-02T03:00:00Z,100\n"
        "c,Start,2024-03-03T00:00:00Z,1\n"
        "c,Work,2024-03-03T01:00:00Z,NaN\n"
        "c,End,2024-03-03T02:00:00Z,100\n"
        "d,Start,2024-03-04T00:00:00Z,7\n"
        "d,Work,2024-03-04T01:00:00Z,9\n"
        "e,Work,2024-03-05T00:00:00Z,6\n"
        "e,End,2024-03-05T01:00:00Z,10\n"
    ).encode()
    selected = workspace.import_log(BytesIO(doc), name="stage", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["amount"]})["log_id"]
    stage = {**metric(), "from_activity": "Start", "to_activity": "End"}
    def analyze(m):
        return query.run(selected, {"level": "cases", "metrics": [m],
            "measures": [{"fn": "sum", "of": "metric:price"}]})
    available = analyze(stage)
    assert available["rows"] == [[8.0]]  # (2+4)/2 + (3+5)/2 + 1
    cov = available["metrics"][0]
    assert cov["flattened_case_event_rows_considered"] == 6
    assert cov["event_rows_with_finite_attribute"] == 5
    assert cov["event_rows_with_missing_invalid_or_nonfinite_attribute"] == 1
    assert cov["cases_without_stage_start"] == 1
    assert cov["cases_without_later_stage_end"] == 1
    assert cov["cases_without_complete_stage"] == 2
    assert cov["cases_with_value"] == 3
    assert cov["cases_with_no_finite_attribute"] == 2
    assert cov["coverage_scope"] == "selected_full_cases_stage_bounded_flattened_case_event_rows"
    assert analyze({**stage, "missing_policy": "complete"})["rows"] == [[7.0]]
    work = analyze({**stage, "activity": "Work"})
    assert work["rows"] == [[9.0]]
    assert work["metrics"][0]["cases_without_matching_activity"] == 0
    assert work["metrics"][0]["coverage_scope"] == (
        "selected_full_cases_stage_bounded_matching_activity_flattened_case_event_rows")
    assert analyze({**stage, "from_activity": "Missing"})["rows"] == [[None]]
    assert analyze({**stage, "to_activity": "Missing"})["rows"] == [[None]]
    saved = query.save(selected, {"name": "Start-to-End stage", "spec": {
        "level": "cases", "metrics": [stage], "measures": [{"fn": "sum", "of": "metric:price"}]}})
    assert query.run(selected, query.list_saved(selected)["analyses"][0]["spec"])["rows"] == [[8.0]]
    template = query.save_metric(selected, {"metric": stage})
    assert query.list_metrics(selected)["metrics"][0]["metric"]["from_activity"] == "Start"
    assert saved["spec"]["metrics"][0]["to_activity"] == "End"
    query.delete_metric(selected, template["metric_id"])
    json.dumps(available, allow_nan=False)


def test_stage_boundaries_tied_timestamp_are_deterministic(log):
    doc = (
        "case,activity,time,amount\n"
        "a,Start,2024-04-01T00:00:00Z,2\n"
        "a,Work,2024-04-01T00:00:00Z,4\n"
        "a,End,2024-04-01T00:00:00Z,100\n"
    ).encode()
    selected = workspace.import_log(BytesIO(doc), name="tied-stage", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["amount"]})["log_id"]
    spec = {"level": "cases", "metrics": [{**metric(), "from_activity": "Start",
        "to_activity": "End"}], "measures": [{"fn": "sum", "of": "metric:price"}]}
    first = query.run(selected, spec)
    assert all(query.run(selected, spec)["rows"] == first["rows"] for _ in range(3))
    assert first["metrics"][0]["flattened_case_event_rows_considered"] <= 2


@pytest.mark.parametrize("fields", [
    {"from_activity": "Start"}, {"to_activity": "End"},
    {"from_activity": "", "to_activity": "End"},
    {"from_activity": "Start", "to_activity": ""},
    {"from_activity": "Start", "to_activity": "End", "sql": "DROP TABLE ce"},
])
def test_stage_contract_requires_both_valid_boundaries_before_dataset(monkeypatch, fields):
    monkeypatch.setattr(workspace, "_query", lambda *a, **kw: pytest.fail("dataset accessed"))
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.run("unused", {"level": "cases", "metrics": [{**metric(), **fields}],
            "measures": [{"fn": "sum", "of": "metric:price"}]})


def test_last_completed_stage_compares_rework_cycles_and_reports_unfinished_tail(log):
    doc = (
        "case,activity,time,amount\n"
        "a,Start,2024-05-01T00:00:00Z,1\n"
        "a,Work,2024-05-01T01:00:00Z,2\n"
        "a,End,2024-05-01T02:00:00Z,100\n"
        "a,Start,2024-05-01T03:00:00Z,7\n"
        "a,Work,2024-05-01T04:00:00Z,9\n"
        "a,End,2024-05-01T05:00:00Z,100\n"
        "a,Start,2024-05-01T06:00:00Z,50\n"
        "b,Start,2024-05-02T00:00:00Z,3\n"
        "b,Work,2024-05-02T01:00:00Z,5\n"
        "b,End,2024-05-02T02:00:00Z,100\n"
        "b,Start,2024-05-02T03:00:00Z,11\n"
        "b,Work,2024-05-02T04:00:00Z,NaN\n"
        "b,End,2024-05-02T05:00:00Z,100\n"
        "c,Start,2024-05-03T00:00:00Z,4\n"
        "c,Work,2024-05-03T01:00:00Z,6\n"
        "d,Work,2024-05-04T00:00:00Z,8\n"
        "d,End,2024-05-04T01:00:00Z,100\n"
        "e,Start,2024-05-05T00:00:00Z,2\n"
        "e,Start,2024-05-05T01:00:00Z,10\n"
        "e,End,2024-05-05T02:00:00Z,100\n"
    ).encode()
    selected = workspace.import_log(BytesIO(doc), name="rework-stage", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["amount"]})["log_id"]
    first = {**metric(), "from_activity": "Start", "to_activity": "End"}
    last = {**first, "stage_occurrence": "last_completed"}
    def analyze(m):
        return query.run(selected, {"level": "cases", "metrics": [m],
            "measures": [{"fn": "sum", "of": "metric:price"}]})

    assert analyze(first)["rows"] == [[11.5]]
    available = analyze(last)
    assert available["rows"] == [[29.0]]  # a=8, b=11, e=10; trailing a ignored
    coverage = available["metrics"][0]
    assert coverage["definition"]["stage_occurrence"] == "last_completed"
    assert coverage["cases_without_stage_start"] == 1
    assert coverage["cases_without_later_stage_end"] == 1
    assert coverage["cases_without_complete_stage"] == 2
    assert coverage["cases_with_trailing_unfinished_stage"] == 2  # a, c
    assert coverage["flattened_case_event_rows_considered"] == 5
    assert coverage["event_rows_with_finite_attribute"] == 4
    assert coverage["event_rows_with_missing_invalid_or_nonfinite_attribute"] == 1
    assert coverage["cases_with_value"] == 3
    assert coverage["cases_without_value"] == 2
    assert analyze({**last, "missing_policy": "complete"})["rows"] == [[18.0]]
    scoped = analyze({**last, "activity": "Work"})
    assert scoped["rows"] == [[9.0]]
    assert scoped["metrics"][0]["cases_without_matching_activity"] == 1  # e
    assert scoped["metrics"][0]["cases_with_trailing_unfinished_stage"] == 2

    spec = {"level": "cases", "metrics": [last],
            "measures": [{"fn": "sum", "of": "metric:price"}]}
    saved = query.save(selected, {"name": "Latest complete review cycle", "spec": spec})
    assert saved["spec"]["metrics"][0]["stage_occurrence"] == "last_completed"
    assert query.run(selected, query.list_saved(selected)["analyses"][0]["spec"])["rows"] == [[29.0]]
    template = query.save_metric(selected, {"metric": last})
    assert query.list_metrics(selected)["metrics"][0]["metric"]["stage_occurrence"] == "last_completed"
    query.delete_metric(selected, template["metric_id"])
    json.dumps(available, allow_nan=False)


def test_same_activity_stage_last_completed_and_unfinished_final_occurrence(log):
    doc = (
        "case,activity,time,amount\n"
        "a,Review,2024-06-01T00:00:00Z,1\n"
        "a,Review,2024-06-01T01:00:00Z,3\n"
        "a,Review,2024-06-01T02:00:00Z,5\n"
        "b,Review,2024-06-02T00:00:00Z,7\n"
    ).encode()
    selected = workspace.import_log(BytesIO(doc), name="same-stage", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["amount"]})["log_id"]
    spec = {"level": "cases", "metrics": [{**metric(), "from_activity": "Review",
        "to_activity": "Review", "stage_occurrence": "last_completed"}],
        "measures": [{"fn": "sum", "of": "metric:price"}]}
    response = query.run(selected, spec)
    assert response["rows"] == [[3.0]]  # second Review until third Review
    assert response["metrics"][0]["cases_with_trailing_unfinished_stage"] == 2
    assert response["metrics"][0]["cases_without_later_stage_end"] == 1


@pytest.mark.parametrize("fields", [
    {"stage_occurrence": "last_completed"},
    {"stage_occurrence": "unknown", "from_activity": "A", "to_activity": "B"},
    {"stage_occurrence": 1, "from_activity": "A", "to_activity": "B"},
    {"stage_occurrence": "last_completed", "from_activity": "A"},
])
def test_stage_occurrence_rejects_invalid_contract_before_dataset(monkeypatch, fields):
    monkeypatch.setattr(workspace, "_query", lambda *a, **kw: pytest.fail("dataset accessed"))
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.run("unused", {"level": "cases", "metrics": [{**metric(), **fields}],
            "measures": [{"fn": "sum", "of": "metric:price"}]})


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
    for activity, expected in (("A", 15.0), ("B", 20.0), ("Unseen", None)):
        scoped = client.post(base + "/analysis", json={**spec, "metrics": [{**m, "activity": activity}]},
                             headers=headers)
        assert scoped.status_code == 200, scoped.text
        assert scoped.json()["rows"] == [[expected]]
        assert scoped.json()["metrics"][0]["definition"]["activity"] == activity
        if activity == "Unseen":
            assert scoped.json()["metrics"][0]["cases_without_matching_activity"] == 2
    stage = {**m, "from_activity": "A", "to_activity": "B"}
    stage_response = client.post(base + "/analysis", json={**spec, "metrics": [stage]}, headers=headers)
    assert stage_response.status_code == 200, stage_response.text
    assert stage_response.json()["rows"] == [[15.0]]
    assert stage_response.json()["metrics"][0]["definition"]["from_activity"] == "A"
    latest_stage = {**stage, "stage_occurrence": "last_completed"}
    latest = client.post(base + "/analysis", json={**spec, "metrics": [latest_stage]}, headers=headers)
    assert latest.status_code == 200, latest.text
    assert latest.json()["rows"] == [[15.0]]
    assert latest.json()["metrics"][0]["definition"]["stage_occurrence"] == "last_completed"
    assert latest.json()["metrics"][0]["cases_with_trailing_unfinished_stage"] == 0
    assert client.post(base + "/metrics", json={"metric": latest_stage}, headers=headers).status_code == 200
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
