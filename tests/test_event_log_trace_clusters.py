"""Trace clustering of a saved event log, and applying a cluster as a case filter."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_analytics as analytics
from server import event_log_cohorts as cohorts
from server import event_log_trace_clusters as trace_clusters
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 3, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _cases():
    """Two ways of working: 30 standard orders (with small variations) and 12 returns that take far longer."""
    cases = {}
    for i in range(30):
        steps = ["Receive order", "Check stock", "Pick", "Ship", "Invoice"]
        if i % 3 == 1:
            steps = ["Receive order", "Check stock", "Pick", "Pack", "Ship", "Invoice"]
        elif i % 5 == 2:
            steps = ["Receive order", "Pick", "Ship", "Invoice"]
        cases[f"o{i:02d}"] = (steps, 1)
    for i in range(12):
        steps = ["Receive order", "Request return", "Inspect", "Refund"]
        if i % 4 == 3:
            steps = ["Receive order", "Request return", "Inspect", "Inspect", "Refund"]
        cases[f"r{i:02d}"] = (steps, 24)
    return cases


def _log(cases=None):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["case", "activity", "time"])
    for n, (case, (steps, hours)) in enumerate((cases or _cases()).items()):
        for k, activity in enumerate(steps):
            writer.writerow([case, activity, (START + timedelta(days=n, hours=k * hours)).isoformat()])
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="orders", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def test_two_ways_of_working_are_separated_and_described():
    log = _log()
    result = trace_clusters.clusters(log)
    assert result["cases"] == 42 and result["k_chosen_by"] == "silhouette" and result["k"] == 2
    assert result["silhouette"] > 0.4
    big, small = result["clusters"]
    assert (big["cases"], small["cases"]) == (30, 12) and abs(big["share"] + small["share"] - 1) < 1e-9
    assert {tuple(v["activities"]) for v in small["top_variants"]} == {
        ("Receive order", "Request return", "Inspect", "Refund"),
        ("Receive order", "Request return", "Inspect", "Inspect", "Refund")}
    more = {a["activity"]: a for a in small["more_common_activities"]}
    assert more["Request return"]["case_share_in_cluster"] == 1.0 and more["Request return"]["lift"] == 3.5
    assert "Ship" in {a["activity"] for a in small["less_common_activities"]}
    assert small["median_throughput_seconds"] > big["median_throughput_seconds"]
    assert small["mean_events_per_case"] == pytest.approx((9 * 4 + 3 * 5) / 12)
    assert "not process types" in result["interpretation"]
    # Deterministic: the same log gives the same clusters.
    assert trace_clusters.clusters(log)["clusters"] == result["clusters"]


def test_a_cluster_cohort_filters_the_other_analyses_to_its_cases():
    log = _log()
    small = trace_clusters.clusters(log)["clusters"][1]
    filtered = analytics.kpis(log, cohort=small["cohort"])
    benchmark = cohorts.benchmark(log, [small["cohort"], {}])
    assert benchmark["cohorts"][0]["cases"] == 12 and benchmark["cohorts"][1]["cases"] == 42
    assert filtered["cases"] == 12
    with pytest.raises(workspace.EventLogWorkspaceError):
        cohorts.parse_cohort({"variants": [[]]})
    with pytest.raises(workspace.EventLogWorkspaceError):
        cohorts.parse_cohort({"variants": "Receive order"})


def test_given_k_rare_variants_and_limits():
    log = _log()
    three = trace_clusters.clusters(log, {"k": 3})
    assert three["k_chosen_by"] == "given" and three["k"] == 3 and sum(c["cases"] for c in three["clusters"]) == 42
    capped = trace_clusters.clusters(log, {"max_variants": 10, "max_activities": 3})
    assert capped["variants"] == 5 and capped["variants_assigned_to_nearest"] == 0
    assert capped["activities_in_features"] == 3
    one = _log({"a": (["X", "Y"], 1), "b": (["X", "Y"], 1)})
    single = trace_clusters.clusters(one)
    assert single["k"] == 1 and single["clusters"][0]["cases"] == 2
    filtered = trace_clusters.clusters(log, {"cohort": {"activity": "Refund"}})
    assert filtered["cases"] == 12
    for bad in ({"k": 1}, {"max_variants": 1}, {"unknown": True}, {"cohort": {"nope": 1}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            trace_clusters.clusters(log, bad)


def test_rare_variants_join_their_nearest_cluster():
    cases = _cases()
    cases["odd1"] = (["Receive order", "Request return", "Inspect", "Refund", "Refund"], 24)
    cases["odd2"] = (["Receive order", "Check stock", "Pick", "Pack", "Pack", "Ship", "Invoice"], 1)
    log = _log(cases)
    result = trace_clusters.clusters(log, {"max_variants": 5, "k": 2})
    assert result["variants_clustered"] == 5 and result["variants_assigned_to_nearest"] == 2
    sizes = sorted(c["cases"] for c in result["clusters"])
    assert sum(sizes) == 44
    returns = next(c for c in result["clusters"] if any("Refund" in v["activities"] for v in c["top_variants"]))
    assert returns["cases"] == 13, "the odd return joins the returns, the odd order the orders"


def test_case_pages_include_rare_variants_even_without_a_cohort_export(monkeypatch):
    log = _log()
    monkeypatch.setattr(cohorts, "MAX_COHORT_VARIANTS", 1)
    spec = {"max_variants": 2, "k": 2}
    result = trace_clusters.clusters(log, spec)
    assert result["variants_assigned_to_nearest"] == 3
    assert any(cluster["cohort"] is None for cluster in result["clusters"])
    members = []
    for cluster in result["clusters"]:
        selection = {**spec, "cluster_key": cluster["cluster_key"], "limit": 4}
        first = trace_clusters.cluster_cases(log, selection)
        assert first["total"] == cluster["cases"]
        ids, cursor = [case["case_id"] for case in first["cases"]], first["next_after"]
        while cursor is not None:
            page = trace_clusters.cluster_cases(log, {**selection, "after": cursor})
            ids.extend(item["case_id"] for item in page["cases"])
            cursor = page["next_after"]
        assert ids == sorted(set(ids)) and len(ids) == cluster["cases"]
        members.extend(ids)
    assert sorted(members) == sorted(_cases())


def test_case_membership_preserves_literal_activity_paths_and_rejects_stale_keys():
    literal = "A → B"
    log = _log({"c0": ([literal, "Finish"], 1), "c1": (["A", "B", "Finish"], 1),
                "c2": ([literal, "Finish"], 1)})
    result = trace_clusters.clusters(log, {"k": 2})
    assert result["variants"] == 2
    clustered = next(c for c in result["clusters"] if c["top_variants"][0]["activities"][0] == literal)
    assert analytics.kpis(log, cohort={"variants": [[literal, "Finish"]]})["cases"] == 2
    selection = {"k": 2, "cluster_key": clustered["cluster_key"]}
    page = trace_clusters.cluster_cases(log, selection)
    assert [row["case_id"] for row in page["cases"]] == ["c0", "c2"]
    assert page["cases"][0]["first_activity"] == literal and page["cases"][0]["last_activity"] == "Finish"
    with pytest.raises(workspace.EventLogWorkspaceError, match="cluster has changed"):
        trace_clusters.cluster_cases(log, {**selection, "cohort": {"activity": literal, "started_before": "2024-03-02T00:00:00Z"}})
    with pytest.raises(workspace.EventLogWorkspaceError, match="limit"):
        trace_clusters.cluster_cases(log, {**selection, "limit": True})


def test_cluster_case_csv_respects_scope_limits_and_spreadsheet_safety(monkeypatch):
    cases = {"=unsafe": (["+Start", "Finish"], 1), "normal": (["+Start", "Finish"], 1),
             "other": (["Different", "End"], 2)}
    log = _log(cases)
    found = trace_clusters.clusters(log, {"k": 2})
    cluster = next(c for c in found["clusters"] if c["cases"] == 2)
    spec = {"k": 2, "cluster_key": cluster["cluster_key"]}
    csv_text, info = trace_clusters.cluster_cases_csv(log, spec)
    rows = list(csv.DictReader(StringIO(csv_text)))
    assert [r["case_id"] for r in rows] == ["'=unsafe", "normal"]
    assert all(r["first_activity"] == "'+Start" and r["last_activity"] == "Finish" for r in rows)
    assert info["total"] == info["written"] == 2 and info["truncated"] is False
    scoped_settings = {"cohort": {"activity": "+Start"}}
    scoped_cluster = trace_clusters.clusters(log, scoped_settings)["clusters"][0]
    scoped_text, scoped = trace_clusters.cluster_cases_csv(log, {**scoped_settings, "cluster_key": scoped_cluster["cluster_key"]})
    assert scoped["total"] == 2 and {row["case_id"] for row in csv.DictReader(StringIO(scoped_text))} == {"'=unsafe", "normal"}
    monkeypatch.setattr(trace_clusters, "MAX_EXPORT_CASES", 1)
    limited, metadata = trace_clusters.cluster_cases_csv(log, spec)
    assert len(list(csv.DictReader(StringIO(limited)))) == 1
    assert metadata["total"] == 2 and metadata["written"] == 1 and metadata["truncated"] is True
    assert metadata["next_after"]
    next_text, last = trace_clusters.cluster_cases_csv(log, {**spec, "after": metadata["next_after"]})
    assert [row["case_id"] for row in csv.DictReader(StringIO(next_text))] == ["normal"]
    assert last["written"] == 1 and last["truncated"] is False and last["next_after"] is None
    with pytest.raises(workspace.EventLogWorkspaceError, match="cursor"):
        trace_clusters.cluster_cases_csv(log, {**spec, "after": "invalid"})
    with pytest.raises(workspace.EventLogWorkspaceError, match="cursor"):
        trace_clusters.cluster_cases_csv(log, {**spec, "after": metadata["next_after"], "max_activities": 3})
    other_log = _log(cases)
    with pytest.raises(workspace.EventLogWorkspaceError, match="cursor"):
        trace_clusters.cluster_cases_csv(other_log, {**spec, "after": metadata["next_after"]})
    with pytest.raises(workspace.EventLogWorkspaceError, match="cluster has changed"):
        trace_clusters.cluster_cases_csv(log, {**spec, "cluster_key": "0" * 64})
    with pytest.raises(workspace.EventLogWorkspaceError):
        trace_clusters.cluster_cases_csv(log, {**spec, "after": "normal"})


def test_route_requires_the_dashboard(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
for i in range(12):
    steps = ["A", "B", "C"] if i % 2 else ["A", "X", "Y", "Z"]
    rows += [f"c{i},{a},2024-03-01T0{k}:00:00Z" for k, a in enumerate(steps)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    log_id = imported.json()["log_id"]
    path = f"/v1/event-logs/{log_id}/trace-clusters"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    found = client.post(path, json={}, headers=owner)
    assert found.status_code == 200, found.text
    assert [c["cases"] for c in found.json()["clusters"]] == [6, 6]
    selected = {"cluster_key": found.json()["clusters"][0]["cluster_key"], "limit": 1}
    assert client.post(path+"/cases", json=selected, headers=bearer).status_code == 403
    export = {"cluster_key": selected["cluster_key"]}
    assert client.post(path+"/cases.csv", json=export, headers=bearer).status_code == 403
    result = client.post(path+"/cases.csv", json=export, headers=owner)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.headers["x-owg-cluster-cases"] == "total=6; written=6; truncated=false"
    assert len(result.text.splitlines()) == 7
    assert client.post(path+"/cases.csv", json={"cluster_key":"0"*64}, headers=owner).status_code == 422
    cases = client.post(path+"/cases", json=selected, headers=owner)
    assert cases.status_code == 200 and cases.json()["total"] == 6 and len(cases.json()["cases"]) == 1, cases.text
    assert client.post(path, json={"k": 99}, headers=owner).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
