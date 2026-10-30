"""Cohort filters and cohort benchmarking for the event-log workspace (roadmap #326 C)."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_analytics as analytics
from server import event_log_cohorts as cohorts
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    return workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv",
                                mapping=MAPPING)["log_id"]


@pytest.mark.parametrize("spec", [
    "x", {"unknown": 1}, {"started_from": "2024-01-01"}, {"started_from": "yesterday"},
    {"attribute": "no-equals"}, {"attribute": "=x"}, {"resource": ""} | {"activity": "a" * 201},
    {"started_from": "2024-02-01T00:00:00Z", "started_before": "2024-01-01T00:00:00Z"},
])
def test_invalid_cohorts_are_refused(spec):
    with pytest.raises(workspace.EventLogWorkspaceError):
        cohorts.parse_cohort(spec)


def test_empty_cohort_means_every_case():
    log_id = _log()
    assert cohorts.parse_cohort(None) == {} and cohorts.parse_cohort({"resource": "", "label": ""}) == {}
    assert analytics.kpis(log_id, cohort={})["cases"] == analytics.kpis(log_id)["cases"] == 60


def test_kpi_filters_by_period_resource_activity_and_attribute():
    log_id = _log()
    # Cases start on 1 + c % 20 January, so days 11-20 hold half the cases.
    assert analytics.kpis(log_id, cohort={"started_from": "2024-01-11T00:00:00Z"})["cases"] == 30
    period = analytics.kpis(log_id, cohort={"started_from": "2024-01-11T00:00:00Z",
                                            "started_before": "2024-01-12T00:00:00Z"})
    assert period["cases"] == 3 and period["cohort"]["started_before"] == "2024-01-12T00:00:00Z"
    ann = analytics.kpis(log_id, cohort={"resource": "ann"})
    assert ann["cases"] == 20 and ann["rework_rate"] == 1.0
    assert analytics.kpis(log_id, cohort={"activity": "Fix"})["cases"] == 20
    assert analytics.kpis(log_id, cohort={"activity": "Fix", "resource": "bob"})["cases"] == 0
    assert analytics.kpis(log_id, cohort={"resource": "nobody"})["throughput_seconds"] is None


def test_attribute_cohorts_use_case_object_attributes():
    objects, events = [], []
    for i in range(20):
        objects.append({"id": f"o{i}", "type": "order",
                        "attributes": [{"name": "region", "value": "EU" if i % 4 == 0 else "US", "time": "2024-01-01T00:00:00Z"}]})
        events.append({"id": f"e{i}", "type": "place", "time": "2024-01-01T09:00:00Z",
                       "relationships": [{"objectId": f"o{i}", "qualifier": "order"}]})
    ocel = {"objectTypes": [{"name": "order", "attributes": []}], "eventTypes": [], "objects": objects, "events": events}
    log_id = workspace.import_log(BytesIO(json.dumps(ocel).encode()), name="o", source_format="ocel")["log_id"]
    assert analytics.kpis(log_id, "order", cohort={"attribute": "region=EU"})["cases"] == 5
    assert analytics.kpis(log_id, "order", cohort={"attribute": "region = US"})["cases"] == 15


def test_benchmark_compares_two_cohorts_with_intervals():
    log_id = _log()
    result = cohorts.benchmark(log_id, [{"label": "bob", "resource": "bob"}, {"label": "ann", "resource": "ann"}],
                               sla_seconds=4 * 3600)
    first, second = result["cohorts"]
    assert (first["cases"], second["cases"]) == (40, 20) and result["overlapping_cases"] == 0
    assert first["definition"] == {"label": "bob", "resource": "bob"}
    diff = result["differences_second_minus_first"]
    assert diff["rework_rate"]["difference"] == 1.0 and diff["rework_rate"]["ci95"][0] > 0.8
    assert diff["sla_breach_rate"]["difference"] == 1.0
    # Fast cases take 3 x 15 min; slow ones 5 x 3 h.
    assert diff["median_throughput_seconds"]["difference"] == 15 * 3600 - 45 * 60
    fix = result["activity_presence"][0]
    assert fix["activity"] == "Fix" and fix["ci_excludes_zero"] is True
    approve = next(a for a in result["activity_presence"] if a["activity"] == "Approve")
    assert approve["ci_excludes_zero"] is False
    assert result["intervals_assume_independent_cohorts"] is True and result["causal_claims"] is False


def test_benchmark_throughput_interval_discloses_actual_case_sample_support(monkeypatch):
    """A capped bootstrap must not imply full-cohort statistical support."""
    log_id = _log()
    specs = [{"resource": "bob"}, {"resource": "ann"}]
    full = cohorts.benchmark(log_id, specs)
    full_gap = full["differences_second_minus_first"]["median_throughput_seconds"]
    assert (full_gap["sampled_cases_first"], full_gap["sampled_cases_second"]) == (40, 20)
    assert not full_gap["sampling_truncated_first"]
    assert not full_gap["sampling_truncated_second"]
    assert full_gap["sampled_median_throughput_seconds_first"] == full["cohorts"][0]["throughput_seconds"]["p50"]
    assert full_gap["sampled_median_throughput_seconds_second"] == full["cohorts"][1]["throughput_seconds"]["p50"]

    # A cap between the cohort sizes truncates only one side.
    monkeypatch.setattr(cohorts, "BOOTSTRAP_SAMPLE", 30)
    partial = cohorts.benchmark(log_id, specs)["differences_second_minus_first"]["median_throughput_seconds"]
    assert (partial["sampled_cases_first"], partial["sampled_cases_second"]) == (30, 20)
    assert partial["sampling_truncated_first"] and not partial["sampling_truncated_second"]

    monkeypatch.setattr(cohorts, "BOOTSTRAP_SAMPLE", 3)
    capped = cohorts.benchmark(log_id, specs)
    gap = capped["differences_second_minus_first"]["median_throughput_seconds"]
    assert [side["cases"] for side in capped["cohorts"]] == [40, 20]
    assert (gap["sampled_cases_first"], gap["sampled_cases_second"]) == (3, 3)
    assert gap["sampling_truncated_first"] and gap["sampling_truncated_second"]
    assert gap["difference"] == (gap["sampled_median_throughput_seconds_second"]
                                 - gap["sampled_median_throughput_seconds_first"])
    assert cohorts.benchmark(log_id, specs)["differences_second_minus_first"]["median_throughput_seconds"] == gap
    assert not any("case_id" in key for key in gap)


def test_benchmark_reports_overlap_and_refuses_bad_input():
    log_id = _log()
    overlapping = cohorts.benchmark(log_id, [{}, {"resource": "ann"}])
    assert overlapping["overlapping_cases"] == 20 and "overstate" in overlapping["note"]
    for bad in ([{}], [{}, {}, {}], "x", [{"resource": "nobody"}, {}]):
        with pytest.raises(workspace.EventLogWorkspaceError):
            cohorts.benchmark(log_id, bad)
    for sla in ("x", 0, True, 10 ** 10):
        with pytest.raises(workspace.EventLogWorkspaceError):
            cohorts.benchmark(log_id, [{}, {}], sla_seconds=sla)


def test_median_difference_bootstrap_is_deterministic_and_brackets_the_estimate():
    a = [float(x) for x in range(1, 101)]
    b = [x + 10 for x in a]
    estimate, low, high = cohorts.median_difference(a, b)
    assert estimate == 10 and low <= 10 <= high
    assert cohorts.median_difference(a, b) == (estimate, low, high)


def test_median_difference_bootstrap_is_invariant_to_sample_row_order():
    """Seeded bootstrap intervals must depend on the samples, not SQL row order."""
    first = [1, 10, 100, 2, 20, 200]
    second = [5, 50, 500, 6, 60, 600]
    expected = cohorts.median_difference(first, second)
    assert cohorts.median_difference(list(reversed(first)), second) == expected
    assert cohorts.median_difference(first, list(reversed(second))) == expected
    assert cohorts.median_difference([20, 1, 200, 10, 2, 100], [60, 5, 600, 50, 6, 500]) == expected


def test_real_http_cohort_routes(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

rows = ["case,activity,time,who"]
for i in range(20):
    who = "ann" if i % 2 else "bob"
    rows += [f"c{i},A,2024-01-{1 + i % 10:02d}T08:00:00Z,{who}", f"c{i},B,2024-01-{1 + i % 10:02d}T0{2 + i % 2}:00:00Z,{who}"]
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&resource_column=who",
                       content="\n".join(rows).encode(), headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    base = "/v1/event-logs/" + made.json()["log_id"]
    assert client.get(base + "/kpis?resource=ann", headers=headers).json()["cases"] == 10
    assert client.get(base + "/kpis?started_from=2024-01-06T00:00:00Z", headers=headers).json()["cases"] == 10
    assert client.get(base + "/kpis?started_from=2024-01-06", headers=headers).status_code == 422
    spec = {"cohorts": [{"resource": "bob"}, {"resource": "ann"}], "sla_seconds": 3600}
    assert client.post(base + "/benchmark", json=spec).status_code == 401
    result = client.post(base + "/benchmark", json=spec, headers=headers)
    assert result.status_code == 200, result.text
    assert [c["cases"] for c in result.json()["cohorts"]] == [10, 10]
    gap = result.json()["differences_second_minus_first"]["median_throughput_seconds"]
    assert (gap["sampled_cases_first"], gap["sampled_cases_second"]) == (10, 10)
    assert not gap["sampling_truncated_first"] and not gap["sampling_truncated_second"]
    assert gap["difference"] == (gap["sampled_median_throughput_seconds_second"]
                                 - gap["sampled_median_throughput_seconds_first"])
    assert client.post(base + "/benchmark", json=[1], headers=headers).status_code == 422
    assert client.post(base + "/benchmark", content=b"x", headers={**headers, "Content-Type": "text/plain"}).status_code == 415
    assert client.post(base + "/benchmark", json={"cohorts": [{}, {}], "sla_seconds": "x"}, headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_dashboard_cohort_helpers():
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    check = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/event_log_workspace.js','utf8'),scope);
const m=scope.window.__owgEventLogModel;
const plain=v=>JSON.parse(JSON.stringify(v));
assert.deepEqual(plain(m.cohortSpec({label:' Q1 ',from:'2024-01-01',before:'2024-04-01',resource:'',activity:'Fix',attribute:'region=EU'})),
  {label:'Q1',started_from:'2024-01-01T00:00:00Z',started_before:'2024-04-01T00:00:00Z',activity:'Fix',attribute:'region=EU'});
assert.deepEqual(plain(m.cohortSpec({})),{});
assert.deepEqual(plain(m.cohortSpec({resource:' bot,system ',activity:' Review > '})),{resource:' bot,system ',activity:' Review > '});
assert.throws(()=>m.cohortSpec({from:'2024-02-01',before:'2024-01-01'}));
assert.throws(()=>m.cohortSpec({attribute:'region'}));
assert.equal(m.toCSV([['a','b'],['=1+1','x,\x22y\x22'],[null,'-2']]),'a,b\r\n\'=1+1,\x22x,\x22\x22y\x22\x22\x22\r\n,\'-2\r\n');
"""
    result = subprocess.run(["node", "-e", check], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
